"""
main — public entry point for running agents.

AgentRunner is the only interface that server.py and tui.py need to import.
"""

from loguru import logger

from .config import MODEL
from .session import Session
from .emitters import BaseEmitter
from .model import ModelAdapter, get_adapter
from .factory import AgentFactory
from .managers.hooks import HookContext


# ─── AgentRunner ──────────────────────────────────────────────────────────────


class AgentRunner:
    """
    运行代理的统一入口。

    持有共享的 API 客户端和模型配置，调用方（server.py、tui.py）无需自行管理。

    append_system: 启动时注入的全局 system 文本（从 md 文件读取），每次 run 都会带上。

    用法：
        runner = AgentRunner(append_system="你是一个助手...")
        await runner.run(session, message, emitter)
    """

    def __init__(
        self,
        model: str = MODEL,
        adapter: ModelAdapter | None = None,
        system: str | None = None,
        append_system: bool = False,
    ):
        self.model = model
        self.adapter = adapter
        self.system = system
        self.append_system = append_system

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

        # adapter 解析：显式传入 > session.settings > get_adapter() 默认
        resolved_adapter = self.adapter
        if resolved_adapter is None:
            provider = session.settings.provider or "anthropic"
            provider_config = session.settings.provider_config or {}
            resolved_adapter = get_adapter(provider, **provider_config)

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
        hook_ctx = HookContext(
            session_id=session.id,
            workdir=session.workdir,
            project_root=session.project_root,
            depth=0,
            agent_id="",
            agent_name="orchestrator",
        )
        # hook: session:start（observing）
        await session.hooks.emit_void("session:start", {}, hook_ctx)
        result = await agent.run(message)
        # hook: session:end（observing）
        await session.hooks.emit_void("session:end", {}, hook_ctx)
        logger.info("Run done  | session={} reply_len={}", session.id[:8], len(result))
        return result
