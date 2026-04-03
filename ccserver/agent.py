"""
Agent — 统一的代理抽象，适用于根代理和子代理。

设计：
    AgentContext   每个代理实例的独立状态（messages、depth、id）
    Agent          核心循环 — 根代理和子代理使用完全相同的逻辑

根代理与子代理的差异仅体现在配置参数上，而非代码路径：
    根代理：persist=True，round_limit=MAIN_ROUND_LIMIT，depth=0，拥有 Task 工具
    子代理：persist=False，round_limit=SUB_ROUND_LIMIT，depth≥1，继承所有工具
            _handle_agent() 中的深度检查防止无限递归

入口点：
    AgentFactory.create_root(session, session_manager, emitter) → 根 Agent
    agent.spawn_child(prompt)                                   → 子 Agent
    agent.run(message)                                          → 执行循环
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from loguru import logger

from .config import MODEL, MAIN_ROUND_LIMIT, SUB_ROUND_LIMIT, MAX_DEPTH, RECORD_DIR
from .hooks.loader import HookContext
from .recorder import Recorder
from .session import Session
from .compactor import Compactor
from .utils import _block_get, _normalize_content, gen_uuid
from .tools import ToolResult
from .tools.bt_base import BaseTool
from .core.emitter import BaseEmitter
from .core.emitter.filter_emitter import FilterEmitter
from .model import ModelAdapter, get_default_adapter

from typing import List, Dict, Any, Optional, Callable


# ─── AgentContext（代理上下文）────────────────────────────────────────────────


@dataclass
class AgentContext:
    """
    单个代理实例的独立状态。

    根代理：messages = session.messages（同一对象，持久化）
    子代理：messages = 全新列表（临时，任务结束后丢弃）

    depth 记录嵌套层级，防止无限递归生成子代理。
    """

    agent_id: str = field(default_factory=lambda: str(gen_uuid()))
    name: str = ""                  # 代理名称，用于日志标识
    messages: list = field(default_factory=list)
    depth: int = 0
    parent_id: str | None = None    # 父代理的 agent_id
    parent_name: str | None = None  # 父代理的 name，便于日志追踪

    @property
    def is_orchestrator(self) -> bool:
        """根代理（depth=0）即编排者，无需显式标记。"""
        return self.depth == 0


# ─── Agent（代理）─────────────────────────────────────────────────────────────


class Agent:
    """
    一个代理实例 = 一个独立上下文 + 一套工具集 + 一个执行循环。

    根代理和子代理使用同一个类，循环逻辑（_loop）完全相同。
    能力差异通过构造参数体现：

        tools       dict[str, BaseTool]  — 代理可使用的工具
        round_limit int                  — 最多可运行的轮次
        persist     bool                 — 消息是否写入磁盘
        system      str                  — 代理的身份 / 指令

    使用 create_root() 构建根代理，使用 spawn_child() 派生子代理。
    """

    def __init__(
        self,
        *,
        session: Session,                       # 当前会话，包含 workdir / 元数据等
        adapter: ModelAdapter,                  # LLM 调用适配器
        emitter: BaseEmitter,                   # 事件发射器，向外推送 token，流式 token
        tools: dict[str, BaseTool],             # 工具集，key 为工具名，value 为工具实例
        context: AgentContext,                  # 独立上下文，持有 name / depth / id 等身份信息
        disabled_tools: dict[str, BaseTool] | None = None,  # 被禁用的工具，生成占位 schema 告知 LLM
        model: str = MODEL,                     # 使用的 LLM 模型名称
        round_limit: int = MAIN_ROUND_LIMIT,    # 最大执行轮次，防止无限循环
        persist: bool = True,                   # 是否将消息持久化到磁盘（子代理为 False）
        prompt_version: str = "cc_reverse:v2.1.81", # 使用哪个 prompt lib
        language: str = "简体中文",              # system prompt 语言
        system: str | None = None,  # 注入的 system 块，由 lib 内部决定如何处理
        append_system: bool = False,                 # True=追加到 workflow 末尾，False=替换 workflow
        skills_override: Optional[List[str]] = None, # 指定可用 skill 名称列表；None = 使用 session 全局 skills
        is_spawn: bool = False,                      # True 表示子代理，False 表示根代理
        run_mode: str | None = None,                 # "auto" 或 "interactive"；None 时从 session.settings 读取
        limit_strategy: str = "last_text",           # round limit 兜底策略
        on_limit_callback: Optional[Callable] = None, # 自定义接管回调，callback 策略时调用
    ):
        self.session:Session = session
        self.adapter:ModelAdapter = adapter
        self.emitter:BaseEmitter = emitter
        self.tools:Dict[str, Any] = tools
        self.context:AgentContext = context
        self.model:str = model
        self.round_limit:int = round_limit
        self.persist:bool = persist
        self.prompt_version:str = prompt_version
        self.skills_override:Optional[List[str]] = skills_override  # None = 用 session.skills；[] = 无 skills
        self.short_aid:str = self.context.agent_id.replace("-", "")[:5]  # 取 agent_id 前5位作为 cch
        # 统一日志标签：id(name)，name 未设置时显示 id(unnamed)
        _aid8 = self.context.agent_id[:8]
        _aname = self.context.name or "unnamed"
        self.aid_label:str = f"{_aid8}({_aname})"
        self.limit_strategy: str = limit_strategy
        self.on_limit_callback: Optional[Callable] = on_limit_callback
        # run_mode：None 时从 session.settings 读取；子代理强制 "auto"（不允许阻塞等待用户）
        if run_mode is not None:
            self.run_mode: str = run_mode
        else:
            self.run_mode = session.settings.run_mode

        from ccserver.prompts_lib.adapter import get_lib
        lib = get_lib(prompt_version)
        self.system:List[Dict[str, Any]] = lib.build_system(session, model, language, cch=self.short_aid, injected_system=system, append_system=append_system, is_spawn=is_spawn)

        self.compactor = Compactor(adapter=adapter, model=model)

        # 缓存 schema 列表 — 只计算一次，每次 LLM 调用复用
        # 内置工具 + 禁用占位；MCP schema 由调用方（factory / spawn_child）追加，
        # 因为 MCP 需要按 settings 过滤，__init__ 不持有 settings 引用。
        disabled_schemas = [t.to_disabled_schema() for t in (disabled_tools or {}).values()]
        # Agent 工具放第一位，与官方工具顺序一致
        agent_schemas = [t.to_schema() for t in tools.values() if t.name == "Agent"]
        other_schemas = [t.to_schema() for t in tools.values() if t.name != "Agent"]
        self._schemas: List[dict] = agent_schemas + other_schemas + disabled_schemas

        self.recorder = Recorder(
            record_dir=RECORD_DIR,
            agent_id=self.context.agent_id,
            agent_name=self.context.name,
            depth=self.context.depth,
            model=self.model,
            system=self.system,
            schemas=self._schemas,
        )

    # ── 公共入口点 ────────────────────────────────────────────────────────────

    async def run(self, message: str) -> str:
        """追加用户消息并执行循环。"""
        if message.startswith("/"):
            await self._handle_command(message)
        else:
            # hook: message:inbound:received — 可修改消息内容、注入 additional_context
            hook_result = await self.session.hooks.emit(
                "message:inbound:received", message, ctx=self._build_hook_ctx()
            )
            if hook_result.message is not None:
                message = hook_result.message
            if hook_result.additional_context:
                # additional_context 追加到消息内容后，让 LLM 能看到
                message = message + "\n\n" + hook_result.additional_context
            self._append({"role": "user", "content": message})
        return await self._loop()

    async def _handle_command(self, raw: str):
        """
        解析 /command [args] 格式的输入，构建 command 消息追加到上下文。

        内置命令（builtin=True）在这里执行前置逻辑（如 /clear 清空历史），
        执行结果作为 stdout 注入消息。
        非内置命令直接将 command 信息传给 lib 包装。
        """
        # 解析 command 名称和参数
        rest = raw[1:]  # 去掉开头的 /
        name, _, args = rest.partition(" ")
        name = name.strip()
        args = args.strip()

        cmd = self.session.commands.get(name)
        stdout = ""

        # 内置命令的前置逻辑
        if cmd and cmd.builtin:
            stdout = await self._run_builtin(name, args)

        body = cmd.load_body() if cmd else ""

        # 将 command 信息作为 dict 传给 _append，由 lib.on_message 负责包装格式
        self._append({
            "role": "user",
            "content": {
                "_type": "command",
                "name": name,
                "args": args,
                "stdout": stdout,
                "body": body,
            },
        })

    async def _run_builtin(self, name: str, args: str) -> str:
        """
        执行内置 command 的前置逻辑，返回 stdout 字符串。
        目前只有 clear 需要特殊处理。
        """
        if name == "clear":
            self.context.messages.clear()
            if self.persist:
                self.session.rewrite_messages([])
            return ""
        return ""

    def spawn_child(self, prompt: str, agent_def=None, agent_name=None, prompt_version: str | None = None, model_override: str | None = None) -> "Agent":
        """
        动态派生一个拥有独立上下文的子代理。

        工具集决策顺序（优先级从高到低）：
          1. CHILD_DISALLOWED_TOOLS（硬编码）：永远禁用，不可被任何配置覆盖
          2. settings.denied_tools（黑名单）：项目/全局配置的禁用工具
          3. agent_def.disallowed_tools（黑名单）：子代理定义的额外禁用
          4. agent_def.tools（白名单） 或 CHILD_DEFAULT_TOOLS（默认白名单）
             is_teammate=True 时叠加 TEAMMATE_EXTRA_TOOLS
          5. settings.allowed_tools（白名单约束）：进一步收紧，None 表示不限制

        mcp（MCP 工具）：
          agent_def.mcp 有值    → 只允许列出的 mcp__server__tool
          agent_def.mcp is None → 不允许任何 MCP（必须显式指定）
          无 agent_def          → 不允许任何 MCP

        skills（注入 catalog）：
          agent_def.skills 有值    → 只注入列出的 skill 名称
          agent_def.skills is None → 不注入任何 skill catalog
          无 agent_def             → 不注入任何 skill catalog

        子代理消息不持久化，拥有更低的轮次上限。
        """
        from .tools.constants import CHILD_DISALLOWED_TOOLS, CHILD_DEFAULT_TOOLS, TEAMMATE_EXTRA_TOOLS

        # ── skills：子代理默认无 skill catalog，除非 agent_def.skills 显式指定 ──
        if agent_def is not None and agent_def.skills is not None:
            child_skills_override = agent_def.skills   # list[str]，可能是空列表
        else:
            child_skills_override = []                 # 不注入任何 skill catalog

        # 子代理的初始消息也要经过 lib.on_message() 处理
        from ccserver.prompts_lib.adapter import get_lib
        initial_message = get_lib(self.prompt_version).on_message(
            {"role": "user", "content": prompt}, self.session, [],
            skills_override=child_skills_override,
        )
        child_context = AgentContext(
            name=agent_name,
            messages=[initial_message],
            depth=self.context.depth + 1,
            parent_id=self.context.agent_id,
            parent_name=self.context.name,
        )

        # ── 内置工具过滤（分层权限决策）────────────────────────────────────
        settings = self.session.settings

        # 步骤 1：确定基础白名单
        if agent_def is not None and agent_def.tools is not None:
            # agent_def 显式指定白名单
            allowed = set(agent_def.tools)
        else:
            # 使用默认白名单
            allowed = set(CHILD_DEFAULT_TOOLS)
            # Teammate 角色额外允许 Task 工具
            if agent_def is not None and agent_def.is_teammate:
                allowed |= TEAMMATE_EXTRA_TOOLS

        # 步骤 2：应用 agent_def 黑名单（在白名单基础上剔除）
        if agent_def is not None and agent_def.disallowed_tools is not None:
            allowed -= set(agent_def.disallowed_tools)

        # 步骤 3：应用 settings 黑名单（项目/全局配置的禁用工具）
        allowed -= settings.denied_tools

        # 步骤 4：应用 settings 白名单约束（None 表示不限制）
        if settings.allowed_tools is not None:
            allowed &= settings.allowed_tools

        # 步骤 5：硬编码永久禁用（最后一道，不可被任何配置绕过）
        allowed -= CHILD_DISALLOWED_TOOLS

        child_tools = {k: v for k, v in self.tools.items() if k in allowed}
        disabled_child_tools = {k: v for k, v in self.tools.items() if k not in child_tools}

        # ── system 注入 ───────────────────────────────────────────────────
        injected_system = None
        if agent_def is not None and agent_def.system:
            injected_system = agent_def.system

        # ── model ─────────────────────────────────────────────────────────
        # 优先级：model_override > agent_def.model > 继承父 agent 的 model
        child_model = model_override or (agent_def.model if agent_def and agent_def.model else None) or self.model

        # emitter：根据 agent_def.output_mode 决定是否包装 FilterEmitter
        child_emitter = self.emitter
        if agent_def is not None and agent_def.output_mode:
            child_emitter = FilterEmitter(self.emitter, mode=agent_def.output_mode)

        child = Agent(
            session=self.session,
            adapter=self.adapter,
            emitter=child_emitter,
            tools=child_tools,
            disabled_tools=disabled_child_tools,
            system=injected_system,
            context=child_context,
            model=child_model,
            round_limit=agent_def.round_limit if agent_def and agent_def.round_limit else SUB_ROUND_LIMIT,
            limit_strategy=agent_def.limit_strategy if agent_def else "last_text",
            persist=False,
            prompt_version=prompt_version or self.prompt_version,
            skills_override=child_skills_override,
            is_spawn=True,
            run_mode="auto",  # 子代理始终 auto，不允许阻塞等待用户确认
        )

        # ── MCP schemas 过滤后追加（子代理 MCP 必须显式指定，agent_def.mcp is None 则无 MCP）──
        if agent_def is not None and agent_def.mcp is not None:
            allowed_mcp = set(agent_def.mcp)
            child._schemas += [s for s in self.session.mcp.schemas() if s["name"] in allowed_mcp]

        # 让 prompt lib 对 schema 描述做后处理（如 cc_reverse 替换为 CC 原版描述）
        from ccserver.prompts_lib.adapter import get_lib
        child._schemas = get_lib(child.prompt_version).patch_tool_schemas(child._schemas)

        child.recorder.schemas = child._schemas

        return child

    # ── 核心循环 ──────────────────────────────────────────────────────────────

    async def _loop(self) -> str:
        # 记录最近一次有内容的 (tokens, text)，供最终轮兜底使用
        last_tokens: list[str] = []
        last_text: str = ""
        logger.debug("Loop start | agent={} depth={} msgs={}", self.aid_label, self.context.depth, len(self.context.messages))
        for round_num in range(self.round_limit):
            await self._maybe_compact()
            logger.debug("Round {}/{} | agent={}", round_num + 1, self.round_limit, self.aid_label)
            # 调用前快照 messages（深拷贝，防止后续 append 污染记录）
            input_messages_snapshot = [dict(m) for m in self.context.messages]
            collected_tokens, response = await self._call_llm_with_retry()
            if response is None:
                return ""

            content = _normalize_content(response.content)
            self.recorder.record(
                round_num + 1,
                input_messages=input_messages_snapshot,
                response_content=content,
                stop_reason=response.stop_reason,
            )
            self._append({"role": "assistant", "content": content})

            round_text = "".join(b["text"] for b in content if b.get("type") == "text")
            if round_text:
                last_tokens = collected_tokens
                last_text = round_text
                # hook: prompt:llm:output — 每轮 LLM 完成后（observing，纯观测）
                await self.session.hooks.emit_void(
                    "prompt:llm:output", round_text, ctx=self._build_hook_ctx()
                )

            if response.stop_reason != "tool_use":
                # 最终轮：推送最近一次有文本的那轮 token
                for token in last_tokens:
                    await self.emitter.emit_token(token)
                logger.debug("Loop done  | agent={} rounds={} reply_len={}", self.aid_label, round_num + 1, len(last_text))
                logger.debug("Loop final_text | agent={} text={!r}", self.aid_label, last_text)
                # 子代理发 subagent_done，根代理发 done，语义区分
                if self.context.is_orchestrator:
                    # hook: agent:stop — 根代理最终完成（observing）
                    await self.session.hooks.emit_void(
                        "agent:stop", last_text, ctx=self._build_hook_ctx()
                    )
                    await self.emitter.emit_done(last_text)
                else:
                    await self.emitter.emit_subagent_done(last_text)
                return last_text

            tool_results, trigger_compact = await self._handle_tools(response.content)
            self._append({"role": "user", "content": tool_results})

            if trigger_compact:
                await self._do_compact(reason="manual compact requested")

        logger.warning("Round limit reached | agent={} limit={}", self.aid_label, self.round_limit)
        return await self._on_limit(last_text, last_tokens)

    async def _on_limit(self, last_text: str, last_tokens: list) -> str:
        """
        round limit 到达时的兜底处理。

        执行顺序：
          1. 触发 agent:limit hook（observing，不影响策略执行）
          2. 若有 on_limit_callback，优先调用，回调返回空则 fallback 到配置策略
          3. 按 limit_strategy 执行对应策略

        策略（主 agent）：
          last_text  — 兜底输出最近一次 last_text，走正常 emit_done 流程
          ask_user   — 向用户询问是否继续，继续则追加一条 user 消息并返回特殊标记触发重入
          graceful   — 向用户输出固定提示，emit_done 结束
          summarize  — 额外调用 LLM 做摘要，把摘要作为最终回复 emit_done
          callback   — 调用 on_limit_callback（无回调时 fallback 到 last_text）

        策略（子 agent）：
          last_text  — 兜底返回最近一次 last_text 给父 agent
          report     — 返回格式化报告给父 agent
          callback   — 调用 on_limit_callback（无回调时 fallback 到 last_text）
        """
        # Step 1：触发 hook（observing，不影响后续）
        await self.session.hooks.emit_void(
            "agent:limit", last_text, ctx=self._build_hook_ctx()
        )

        # Step 2：callback 优先
        if self.on_limit_callback is not None:
            try:
                result = await self.on_limit_callback(self, last_text)
                if result:
                    return await self._finish_with_last_text(result, [result])
            except Exception as e:
                logger.error("on_limit_callback failed | agent={} error={}", self.aid_label, e)
            # 回调失败或返回空，fallback 到 last_text 策略

        strategy = self.limit_strategy

        if strategy == "ask_user":
            return await self._on_limit_ask_user(last_text, last_tokens)
        elif strategy == "graceful":
            return await self._on_limit_graceful(last_text, last_tokens)
        elif strategy == "summarize":
            return await self._on_limit_summarize(last_text, last_tokens)
        elif strategy == "report" and not self.context.is_orchestrator:
            rounds = self.round_limit
            report = f"[LIMIT_REACHED] 已执行 {rounds} 轮，部分结果：{last_text or '（无输出）'}"
            return report
        else:
            # last_text（默认）或 callback 无回调 fallback，或子 agent 用了主 agent 专属策略
            return await self._finish_with_last_text(last_text, last_tokens)

    async def _finish_with_last_text(self, last_text: str, last_tokens: list) -> str:
        """兜底输出 last_text，走正常结束流程。无 last_text 时 emit_error。"""
        if last_text:
            for token in last_tokens:
                await self.emitter.emit_token(token)
            if self.context.is_orchestrator:
                await self.session.hooks.emit_void(
                    "agent:stop", last_text, ctx=self._build_hook_ctx()
                )
                await self.emitter.emit_done(last_text)
            else:
                await self.emitter.emit_subagent_done(last_text)
            return last_text
        else:
            await self.emitter.emit_error("Round limit reached with no output")
            return ""

    async def _on_limit_ask_user(self, last_text: str, last_tokens: list) -> str:
        """向用户询问是否继续（仅主 agent 有意义）。"""
        if not self.context.is_orchestrator:
            return await self._finish_with_last_text(last_text, last_tokens)
        answer = await self.emitter.emit_ask_user([{
            "question": f"已执行 {self.round_limit} 轮仍未完成，是否继续？",
            "header": "继续运行",
            "options": [
                {"label": "继续", "description": "重置轮次计数，继续执行"},
                {"label": "停止", "description": "输出当前结果并结束"},
            ],
            "multiSelect": False,
        }])
        if answer and "继续" in answer:
            # 追加一条 user 消息触发下一轮，重置轮次上限
            self.context.messages.append({"role": "user", "content": "继续执行未完成的任务。"})
            self.round_limit = self.round_limit  # 保持不变，_loop 会重新进入
            # 直接在此递归调用 _loop，让新的 round_limit 生效
            return await self._loop()
        return await self._finish_with_last_text(last_text, last_tokens)

    async def _on_limit_graceful(self, last_text: str, last_tokens: list) -> str:
        """向用户输出固定提示后优雅结束。"""
        graceful_msg = "处理步骤超出限制，请重新提问或简化需求。"
        if last_text:
            graceful_msg = f"{graceful_msg}\n\n目前结果：{last_text}"
        if self.context.is_orchestrator:
            await self.session.hooks.emit_void(
                "agent:stop", graceful_msg, ctx=self._build_hook_ctx()
            )
            await self.emitter.emit_done(graceful_msg)
        else:
            await self.emitter.emit_subagent_done(graceful_msg)
        return graceful_msg

    async def _on_limit_summarize(self, last_text: str, last_tokens: list) -> str:
        """调用 LLM 对当前消息做摘要，以摘要作为最终回复。"""
        try:
            import json as _json
            conversation = _json.dumps(self.context.messages, default=str, ensure_ascii=False)[:20000]
            response = await self.adapter.create(
                model=self.model,
                messages=[{"role": "user", "content": (
                    "请对以下对话做简洁总结，说明已完成了什么、当前状态是什么：\n\n" + conversation
                )}],
                max_tokens=1000,
            )
            summary = response.content[0].text
        except Exception as e:
            logger.error("_on_limit_summarize failed | agent={} error={}", self.aid_label, e)
            return await self._finish_with_last_text(last_text, last_tokens)

        result = f"（步骤超限，以下为当前进度摘要）\n\n{summary}"
        if self.context.is_orchestrator:
            await self.session.hooks.emit_void(
                "agent:stop", result, ctx=self._build_hook_ctx()
            )
            await self.emitter.emit_done(result)
        else:
            await self.emitter.emit_subagent_done(result)
        return result

    # ── 工具处理 ──────────────────────────────────────────────────────────────

    async def _call_llm_with_retry(self):
        """
        调用 LLM，遇到网络临时错误时自动重试，最多 3 次。
        返回 (collected_tokens, response)，失败时返回 ([], None)。

        可重试的错误：连接断开、incomplete read、超时等网络层异常。
        不可重试的错误：认证失败、参数错误等 API 错误（直接返回 None）。
        """
        import asyncio
        from anthropic import APIConnectionError, APITimeoutError

        max_retries = 3
        retry_delays = [2, 5, 10]  # 秒，递增等待
        for attempt in range(max_retries):
            try:
                collected_tokens: list[str] = []
                async with self.adapter.stream(
                    model=self.model,
                    system=self.system,
                    messages=self.context.messages,
                    tools=self._schemas,
                    max_tokens=8000,
                ) as stream:
                    async for text in stream.text_stream:
                        collected_tokens.append(text)
                    response = await stream.get_final_message()
                return collected_tokens, response

            except (APIConnectionError, APITimeoutError) as e:
                # 网络层临时错误，可以重试
                if attempt < max_retries - 1:
                    delay = retry_delays[attempt]
                    logger.warning(
                        "LLM network error, retrying ({}/{}) | agent={} delay={}s error={}",
                        attempt + 1, max_retries, self.aid_label, delay, e,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error("LLM error after {} retries | agent={} error={}", max_retries, self.aid_label, e)
                    await self.emitter.emit_error(str(e))
                    return [], None

            except Exception as e:
                # 其他错误（认证、参数等）不重试
                logger.error("LLM error | agent={} error={}", self.aid_label, e)
                await self.emitter.emit_error(str(e))
                return [], None

        return [], None  # 不会到达，但让类型检查满意

    async def _handle_tools(self, blocks) -> tuple[list[dict], bool]:
        """
        执行响应中所有 tool_use 块。
        返回 (用于 API 的 tool_result 列表, trigger_compact 标志)。

        权限检查（在工具执行前）：
          如果工具名在 settings.ask_tools 中，则根据 run_mode 决定：
            auto        — 直接拒绝（返回错误结果，不执行工具）
            interactive — 推送 permission_request 事件等待用户批准；拒绝则同 auto
        """
        results: list[dict] = []
        trigger_compact = False
        ask_tools = self.session.settings.ask_tools

        for block in blocks:
            if _block_get(block, "type") != "tool_use":
                continue

            name: str = _block_get(block, "name") or ""
            input_: dict = _block_get(block, "input") or {}
            block_id: str = _block_get(block, "id") or ""

            # ── 运行时权限检查 ────────────────────────────────────────────────
            if name in ask_tools:
                if self.run_mode == "interactive":
                    # 向客户端发送权限请求，等待用户决定
                    logger.info("Permission request | agent={} tool={} mode=interactive", self.aid_label, name)
                    granted = await self.emitter.emit_permission_request(name, input_)
                    if not granted:
                        logger.info("Permission denied  | agent={} tool={}", self.aid_label, name)
                        result = ToolResult.error(f"Tool '{name}' was denied by user.")
                        results.append(result.to_api_dict(block_id))
                        continue
                    logger.info("Permission granted | agent={} tool={}", self.aid_label, name)
                else:
                    # auto 模式：直接拒绝，不等待用户
                    logger.info("Permission denied (auto) | agent={} tool={}", self.aid_label, name)
                    result = ToolResult.error(
                        f"Tool '{name}' requires user confirmation but run_mode is 'auto'. "
                        "Add it to permissions.ask and use interactive mode, or remove it from ask_tools."
                    )
                    results.append(result.to_api_dict(block_id))
                    continue

            # hook: tool:call:before — 工具执行前（modifying，可阻断）
            tool_hook = await self.session.hooks.emit(
                "tool:call:before", name, input_, ctx=self._build_hook_ctx()
            )
            if tool_hook.block:
                logger.info("Hook blocked tool | agent={} tool={} reason={}", self.aid_label, name, tool_hook.block_reason)
                result = ToolResult.error(tool_hook.block_reason or f"Tool '{name}' blocked by hook.")
                results.append(result.to_api_dict(block_id))
                continue

            if name.startswith("mcp__"):
                # MCP 工具：显示所有参数，每个 key=value 一项，value 截断 200 字符
                preview_parts = [f"{k}={str(v)[:200]}" for k, v in input_.items()]
                preview = ", ".join(preview_parts)
            else:
                preview = str(list(input_.values())[0])[:80] if input_ else ""
            await self.emitter.emit_tool_start(name, preview)

            if name == "Agent":
                result = await self._handle_agent(input_)
            elif name == "AskUserQuestion":
                result = await self._handle_ask_user(input_)
            elif name == "Compact":
                trigger_compact = True
                result = ToolResult.ok("Compressing...")
            elif name.startswith("mcp__"):
                result = await self._handle_mcp_tool(name, input_)
            else:
                tool = self.tools.get(name)
                if tool:
                    logger.debug("Tool call  | agent={} tool={} input={}", self.aid_label, name, input_)
                    result = await tool(**input_)
                    logger.debug("Tool result| agent={} tool={} result={!r}", self.aid_label, name, result.content[:200] if result.content else "")
                else:
                    logger.warning("Unknown tool | agent={} tool={}", self.aid_label, name)
                    result = ToolResult.error(f"Unknown tool: {name}")

            await self.emitter.emit_tool_result(name, result.content)
            # hook: tool:call:after / tool:call:failure（observing）
            if result.is_error:
                await self.session.hooks.emit_void(
                    "tool:call:failure", name, result.content or "", ctx=self._build_hook_ctx()
                )
            else:
                await self.session.hooks.emit_void(
                    "tool:call:after", name, result.content or "", ctx=self._build_hook_ctx()
                )
            results.append(result.to_api_dict(block_id))

        return results, trigger_compact

    async def _handle_agent(self, task_input: dict) -> ToolResult:
        """派生子代理并运行至完成。"""
        if self.context.depth >= MAX_DEPTH:
            logger.warning("Max depth reached | agent={} depth={}", self.aid_label, self.context.depth)
            return ToolResult.error(
                f"Max agent nesting depth ({MAX_DEPTH}) reached. "
                "Cannot spawn further subagents."
            )
        prompt = task_input.get("prompt", "")
        if not prompt:
            return ToolResult.error("Task requires a non-empty prompt.")

        # 查找 agent_def（如果 Task 工具传入了 agent 名称）
        subagent_type = task_input.get("subagent_type", "")
        agent_name = task_input.get("description", "") or subagent_type
        model_override = task_input.get("model", "") or None
        logger.info("Agent tool called  | parent={} subagent_type={} description={} model={}", self.aid_label, subagent_type or "(generic)", agent_name or "-", model_override or "inherit")
        agent_def = self.session.agents.get(subagent_type) if subagent_type else None
        if subagent_type and agent_def is None:
            logger.warning("Agent def not found | subagent_type={}", subagent_type)

        child = self.spawn_child(prompt, agent_def=agent_def, agent_name=agent_name, model_override=model_override)
        agent_type_label = f"{subagent_type}(defined)" if agent_def else f"{subagent_type}(undefined)" if subagent_type else "(generic)"
        logger.info("Child agent spawned | parent={} child={} depth={} type={}", self.aid_label, child.aid_label, child.context.depth, agent_type_label)
        # hook: subagent:spawning（observing）
        await self.session.hooks.emit_void(
            "subagent:spawning", ctx=child._build_hook_ctx()
        )
        summary = await child._loop()
        logger.info("Child agent done   | child={} summary_len={}", child.context.agent_id[:8], len(summary))
        # hook: subagent:ended（observing）
        await self.session.hooks.emit_void(
            "subagent:ended", summary, ctx=child._build_hook_ctx()
        )
        return ToolResult.ok(summary or "(no summary)")

    async def _handle_ask_user(self, input_: dict) -> ToolResult:
        """
        通过 emitter 向客户端推送提问，等待用户回答后返回答案。

        emitter.emit_ask_user() 负责实际的等待逻辑：
          - SSEEmitter：推送事件后阻塞，直到客户端调用 /chat/stream/answer 注入答案
          - WSEmitter：推送事件后等待客户端发送下一条 {"answer": "..."} 消息
          - CollectEmitter / TUIEmitter：推送事件后立即返回空字符串（不支持交互）
        """
        questions = input_.get("questions", [])
        if not questions:
            return ToolResult.error("AskUserQuestion requires at least one question.")

        logger.info("AskUserQuestion | agent={} questions={}", self.aid_label, len(questions))
        answer = await self.emitter.emit_ask_user(questions)
        logger.info("AskUserQuestion answered | agent={} answer_len={}", self.aid_label, len(answer))

        return ToolResult.ok(answer if answer else "(user did not answer)")

    async def _handle_mcp_tool(self, name: str, input_: dict) -> ToolResult:
        """转发 mcp__<server>__<tool> 调用到对应的 MCP server。"""
        parts = name.split("__", 2)
        if len(parts) != 3:
            return ToolResult.error(f"Invalid MCP tool name: {name}")
        _, server_name, tool_name = parts
        client = self.session.mcp.get_client(server_name)
        if client is None:
            return ToolResult.error(f"MCP server not found: {server_name}")
        logger.debug("MCP call | agent={} server={} tool={} input={}", self.aid_label, server_name, tool_name, input_)
        output = await client.call(tool_name, input_)
        return ToolResult.ok(output)

    # ── 上下文管理 ────────────────────────────────────────────────────────────

    def _append(self, message: dict):
        """
        向上下文追加消息，并根据配置决定是否持久化。

        始终将消息写入 context.messages（对根代理来说，这与 session.messages 是同一对象）。
        persist=True 时额外调用 session.persist_message 将消息写入磁盘。

        消息在写入前统一经过 lib.on_message() 处理（包装格式、注入 reminder 等）。
        """
        from ccserver.prompts_lib.adapter import get_lib
        message = get_lib(self.prompt_version).on_message(
            message, self.session, self.context.messages,
            skills_override=self.skills_override,
        )

        # 始终写入 context.messages
        self.context.messages.append(message)
        # persist=True 时额外写磁盘（只写盘，不重复 append 到列表）
        if self.persist:
            self.session.persist_message(message)

    async def _maybe_compact(self):
        self.compactor.micro(self.context.messages)
        if self.compactor.needs_compact(self.context.messages):
            logger.debug(f"do compact")
            await self._do_compact(reason="token threshold reached")

    async def _do_compact(self, reason: str):
        # hook: agent:compact:before（observing）
        await self.session.hooks.emit_void(
            "agent:compact:before", ctx=self._build_hook_ctx()
        )
        await self.emitter.emit_compact(reason)
        from ccserver.prompts_lib.adapter import get_lib
        lib = get_lib(self.prompt_version)
        compacted = await self.compactor.compact(
            self.session,
            self.emitter,
            self.context.messages,
            lib=lib,
        )
        if self.persist:
            self.session.rewrite_messages(compacted)
        else:
            self.context.messages[:] = compacted
        # hook: agent:compact:after（observing）
        await self.session.hooks.emit_void(
            "agent:compact:after", ctx=self._build_hook_ctx()
        )

    # ── Hook 辅助 ─────────────────────────────────────────────────────────────

    def _build_hook_ctx(self) -> HookContext:
        """构建当前代理的 HookContext，供所有 hook 调用使用。"""
        return HookContext(
            session_id=self.session.id,
            workdir=self.session.workdir,
            project_root=self.session.project_root,
            depth=self.context.depth,
            agent_id=self.context.agent_id,
            agent_name=self.context.name,
            is_orchestrator=self.context.is_orchestrator,
        )

    # ── 调试 ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<Agent id={self.aid_label} "
            f"depth={self.context.depth} "
            f"msgs={len(self.context.messages)} "
            f"persist={self.persist}>"
        )

