"""
main — public entry point for running agents.

AgentRunner is the only interface that server.py and tui.py need to import.
"""

import time
from collections import OrderedDict

from loguru import logger

from .session import Session
from .emitters import BaseEmitter
from .model_engine import ModelAdapter, AdapterFactory
from .factory import AgentFactory
from .managers.hooks import HookContext
from .emitters.filter import FilterEmitter


# ─── Agent LRU 缓存常量 ───────────────────────────────────────────────────────

_AGENT_CACHE_MAX_SIZE: int = 128    # 最多缓存 128 个 Agent 实例
_AGENT_CACHE_TTL_S: float = 3600.0  # 空闲 1 小时后驱逐（单调时间）


# ─── AgentRunner ──────────────────────────────────────────────────────────────


class AgentRunner:
    """
    运行代理的统一入口。

    持有共享的 API 客户端和模型配置，调用方（server.py、tui.py）无需自行管理。

    Agent LRU 缓存（P2-3）
    ─────────────────────
    以 session.id 为键缓存根 Agent 实例（最多 128 个，空闲 1 小时驱逐）。
    命中缓存时复用已有实例，跳过 AgentFactory.create_root()，保留 LLM 客户端
    连接和 Anthropic Prompt Cache 的 session 上下文，可节省约 90% input token。

    命中时仅更新 emitter（每次请求的 emitter 不同），其余状态（context.messages、
    adapter、tools 等）复用。如需强制新建（如 /new 命令清空后），调用 invalidate_agent()。

    append_system: 启动时注入的全局 system 文本（从 md 文件读取），每次 run 都会带上。

    用法：
        runner = AgentRunner(append_system="你是一个助手...")
        await runner.run(session, message, emitter)
    """

    def __init__(
        self,
        model: str | None = None,
        adapter: ModelAdapter | None = None,
        system: str | None = None,
        append_system: bool = False,
    ):
        self.model = model
        self.adapter = adapter
        self.system = system
        self.append_system = append_system
        # LRU 缓存：session_id → (Agent, last_used_monotonic)
        # 使用 OrderedDict 实现 LRU 驱逐（move_to_end + popitem(last=False)）
        self._agent_cache: OrderedDict[str, tuple] = OrderedDict()

    # ── LRU 缓存操作 ──────────────────────────────────────────────────────────

    def _cache_get(self, session_id: str):
        """
        从缓存取 Agent 实例。命中且未过期则返回 Agent，否则返回 None。

        副作用：命中时将条目移到 OrderedDict 末尾（LRU 更新）。
        """
        entry = self._agent_cache.get(session_id)
        if entry is None:
            return None
        agent, last_used = entry
        now = time.monotonic()
        if now - last_used > _AGENT_CACHE_TTL_S:
            # 过期驱逐
            del self._agent_cache[session_id]
            logger.debug(
                "AgentCache expired | session={} idle={:.0f}s",
                session_id[:8], now - last_used,
            )
            return None
        # 命中：移到末尾（最近使用）
        self._agent_cache.move_to_end(session_id)
        self._agent_cache[session_id] = (agent, now)
        return agent

    def _cache_put(self, session_id: str, agent) -> None:
        """
        写入缓存，超出 MAX_SIZE 时驱逐最久未使用的条目（LRU 头部）。
        """
        self._agent_cache[session_id] = (agent, time.monotonic())
        self._agent_cache.move_to_end(session_id)
        # 驱逐超额条目
        while len(self._agent_cache) > _AGENT_CACHE_MAX_SIZE:
            old_id, _ = self._agent_cache.popitem(last=False)
            logger.debug("AgentCache evict (LRU) | session={}", old_id[:8])

    def invalidate_agent(self, session_id: str) -> bool:
        """
        主动让缓存失效（/new 命令清空历史后调用，确保下次创建新 Agent）。

        Args:
            session_id: Session ID

        Returns:
            True 表示有缓存被清除，False 表示缓存本来就不存在
        """
        existed = session_id in self._agent_cache
        self._agent_cache.pop(session_id, None)
        if existed:
            logger.info("AgentCache invalidated | session={}", session_id[:8])
        return existed

    # ── 主运行入口 ────────────────────────────────────────────────────────────

    async def run(
        self,
        session: Session,
        message: str,
        emitter: BaseEmitter,
        prompt_version: str | None = None,
        run_mode: str | None = None,  # None = 从 session.settings.run_mode 读取
    ) -> str:
        logger.info("Run start | session={} message={!r}", session.id[:8], message[:80])
        # 首次调用时连接 MCP server（lazy connect，避免 session 创建时就启动所有进程）
        if session.mcp:
            await session.mcp.connect_all()

        # ── Agent LRU 缓存命中检测 ────────────────────────────────────────────
        agent = self._cache_get(session.id)
        cache_hit = agent is not None

        if cache_hit:
            # 命中：更新 emitter（每次请求的 emitter 不同），复用其余状态
            agent.emitter = emitter
            logger.info(
                "AgentCache hit | session={} agent_id={}",
                session.id[:8], agent.context.agent_id[:8],
            )
        else:
            # 未命中：重新建 Agent
            resolved_adapter = self.adapter
            if resolved_adapter is None:
                endpoint = session.config.model.to_model_endpoint(model_id=self.model)
                resolved_adapter = AdapterFactory.build(endpoint)

            agent = AgentFactory.create_root(
                session,
                emitter,
                model=self.model,
                adapter=resolved_adapter,
                prompt_version=prompt_version,
                system=self.system,
                append_system=self.append_system,
                run_mode=run_mode,
            )
            # 新建后存入缓存
            self._cache_put(session.id, agent)
            logger.info(
                "AgentCache miss — created new | session={} agent_id={}",
                session.id[:8], agent.context.agent_id[:8],
            )

        # Agent 创建或复用后，通知 SSEEmitter / WSEmitter 当前根 Agent 的 ID，
        # 使其能过滤掉子 Agent 的流式事件，防止多 Agent 事件混流。
        # 用 isinstance 穿透 FilterEmitter 包装层，而非 hasattr（MagicMock 会响应任何 hasattr）。
        root_agent_id = agent.context.agent_id
        _inner = emitter
        while isinstance(_inner, FilterEmitter):
            _inner = _inner._inner
        if hasattr(_inner, "set_root_agent_id"):
            _inner.set_root_agent_id(root_agent_id)

        hook_ctx = HookContext(
            session_id=session.id,
            workdir=session.workdir,
            project_root=session.project_root,
            depth=0,
            agent_id=root_agent_id,
            agent_name=agent.context.name or "orchestrator",
        )
        # hook: session:start（observing）
        await session.hooks.emit_void("session:start", {}, hook_ctx)
        result = await agent.run(message)
        # hook: session:end（observing）
        await session.hooks.emit_void("session:end", {}, hook_ctx)
        logger.info(
            "Run done | session={} cache_hit={} reply_len={}",
            session.id[:8], cache_hit, len(result),
        )
        return result
