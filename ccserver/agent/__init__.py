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

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from loguru import logger

from ccserver.managers.hooks import HookContext
from ..recorder import Recorder
from ..session import Session
from ..compact import CompactorFactory
from ..utils import generate_message_id
from ccserver.builtins.tools import ToolResult
from ccserver.builtins.tools import BuiltinTools
from ccserver.emitters import BaseEmitter
from ..agent_handle import BackgroundAgentHandle
from ..event_bus import AgentEvent, EventType, SenderType
from ..model_engine import ModelAdapter
from .runtime import AgentRuntime  # noqa: F401  Agent 拆分后协作者依赖的运行时契约(Protocol)
from .message_builder import MessageBuilder  # L2 造消息器 + 消息净化

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

    agent_id: str = field(default_factory=lambda: str(generate_message_id()))
    name: str = ""                  # 代理名称，用于日志标识
    messages: list = field(default_factory=list)
    depth: int = 0
    parent_id: str | None = None    # 父代理的 agent_id
    parent_name: str | None = None  # 父代理的 name，便于日志追踪
    env_vars: dict[str, str] = field(default_factory=dict)  # 环境变量，子代理继承
    inbox: asyncio.Queue = field(default_factory=asyncio.Queue)  # 外部消息队列（后台任务/ teammate 用）

    @property
    def is_orchestrator(self) -> bool:
        """根代理（depth=0）即编排者，无需显式标记。"""
        return self.depth == 0


# ─── AgentState（代理运行时状态）──────────────────────────────────────────────


@dataclass
class AgentState:
    """
    代理运行时状态，用于外部系统查询 agent 当前的运行阶段。

    phase 取值:
        - idle          : 刚创建，未开始运行
        - running       : 正在运行（循环中）
        - llm_calling   : 正在调用 LLM
        - tool_executing: 正在执行工具
        - done          : 正常结束
        - error         : 异常结束
        - limit_reached : 达到轮次上限
        - cancelled     : 被外部取消
    """
    phase: str = "idle"
    round_num: int = 0
    current_tool: str | None = None
    start_time: datetime | None = None
    last_error: str | None = None


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

    架构说明（重构中）：
        Agent 正逐步退化为「协调者」，把具体职责委托给协作者
        (LimitPolicy / LLMCaller / ToolDispatcher / SpawnManager /
         CompactCoordinator)。Agent 满足 runtime.AgentRuntime 契约
        (structural typing，无需显式继承)，协作者只依赖该最小契约而非整个
        Agent，以降低耦合(LOD)。
    """

    def __init__(
        self,
        *,
        session: Session,                       # 当前会话，包含 workdir / 元数据等
        adapter: ModelAdapter,                  # LLM 调用适配器
        emitter: BaseEmitter,                   # 事件发射器，向外推送 token，流式 token
        tools: dict[str, BuiltinTools],             # 工具集，key 为工具名，value 为工具实例
        context: AgentContext,                  # 独立上下文，持有 name / depth / id 等身份信息
        disabled_tools: dict[str, BuiltinTools] | None = None,  # 被禁用的工具，生成占位 schema 告知 LLM
        model: str = None,                      # 使用的 LLM 模型名称（None=从 session.config 取）
        round_limit: int = None,                # 最大执行轮次（None=从 session.config 取）
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
        stream: bool = True,                        # True=实时 emit token，False=非流式（只返回最终结果）
        env_vars: dict[str, str] | None = None,     # 环境变量，会合并到 context.env_vars
    ):
        self.session:Session = session
        # model / round_limit 未显式传入时，从该会话配置解析（factory/spawn 一般显式传入）
        if model is None:
            model = session.config.model.model_id
        if round_limit is None:
            round_limit = session.config.agent.main_round_limit
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
        self.stream: bool = stream
        self.state: AgentState = AgentState()
        # 合并环境变量
        if env_vars:
            self.context.env_vars.update(env_vars)
        # run_mode：None 时从 session.settings 读取；子代理强制 "auto"（不允许阻塞等待用户）
        if run_mode is not None:
            self.run_mode: str = run_mode
        else:
            self.run_mode = session.config.agent.run_mode

        from ccserver.prompt_engine import PromptEngine
        self.prompt_engine: PromptEngine = PromptEngine(prompt_version)
        self.system:List[Dict[str, Any]] = self.prompt_engine.build_system(session, model, language, cch=self.short_aid, injected_system=system, append_system=append_system, is_spawn=is_spawn)

        self.compactor = CompactorFactory.build_default(adapter=adapter, model=model)
        # 上次收到 assistant 消息的时间，供 micro 时间触发压缩使用
        self._last_assistant_time: datetime | None = None
        # 协作者:压缩协调器(Step 2 拆出,Agent 仅持有并委托)
        from .compact_coordinator import CompactCoordinator
        self._compact_coordinator = CompactCoordinator(self, self.compactor)
        # L2 造消息器：build hook + sanitize + input hook
        from .message_builder import MessageBuilder
        self._message_builder = MessageBuilder(self)
        # L1 健壮 LLM 客户端：重试 / 流式 / extract_text（零 agent 依赖）
        from ccserver.model_engine.client import LLMCaller
        # 注意：system 仅作默认占位，每轮调用都用 build() 产出的 effective_system 覆盖
        self._llm_caller = LLMCaller(self.adapter, model=self.model, system=self.system, max_tokens=8000)
        # 协作者:派生管理器(Step 4 拆出,负责 spawn_child/background/teammate)
        from .spawn_manager import SpawnManager
        self._spawn_manager = SpawnManager(self)
        # 协作者:工具分发器(Step 5 拆出,负责 _handle_tools 及各工具处理)
        from .tool_dispatcher import ToolDispatcher
        self._tool_dispatcher = ToolDispatcher(self)
        # 协作者:Team inbox 协调器(拆出 _drain_inbox_and_respond)
        from .team_coordinator import TeamCoordinator
        self._team_coordinator = TeamCoordinator(self)

        # 缓存 schema 列表 — 只计算一次，每次 LLM 调用复用
        # 内置工具 + 禁用占位；MCP schema 由调用方（factory / spawn_child）追加，
        # 因为 MCP 需要按 settings 过滤，__init__ 不持有 settings 引用。
        disabled_schemas = [t.to_disabled_schema() for t in (disabled_tools or {}).values()]
        # Agent 工具放第一位，与官方工具顺序一致
        agent_schemas = [t.to_schema() for t in tools.values() if t.name == "Agent"]
        other_schemas = [t.to_schema() for t in tools.values() if t.name != "Agent"]
        self._schemas: List[dict] = agent_schemas + other_schemas + disabled_schemas

        self.recorder = Recorder(
            record_dir=session.config.infra.record_dir,
            agent_id=self.context.agent_id,
            agent_name=self.context.name,
            depth=self.context.depth,
            model=self.model,
            system=self.system,
            schemas=self._schemas,
        )

        # 协作者:轮次上限兜底策略(Step 1 拆出,Agent 仅持有并委托)
        from .limit_policy import LimitPolicy
        self._limit_policy = LimitPolicy(self)

    # ── 内部辅助 ──────────────────────────────────────────────────────────────

    async def _set_phase(self, new_phase: str) -> None:
        """
        设置 Agent 状态并发布 phase_changed 事件到 EventBus。

        所有 phase 变化必须通过此方法，确保 monitor 能实时追踪 Agent 状态。

        Args:
            new_phase: 新状态，取值见 AgentState.phase 文档。
        """
        old_phase = self.state.phase
        if old_phase == new_phase:
            return
        self.state.phase = new_phase
        await self.session.event_bus.publish(AgentEvent(
            type=EventType.PHASE_CHANGED,
            agent_id=self.context.agent_id,
            session_id=self.session.id,
            sender_type=SenderType.AGENT,
            payload={
                "from_phase": old_phase,
                "to_phase": new_phase,
                "round_num": self.state.round_num,
                "current_tool": self.state.current_tool,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        ))
        logger.debug(
            "Phase changed | agent={} {} -> {}",
            self.aid_label, old_phase, new_phase,
        )

    async def _publish_llm_retry(self, attempt, error):
        """
        重试可观测回调：L1 LLMCaller 每次重试前调用，发 LLM_RETRY 事件到 event_bus。

        Args:
            attempt: 第几次重试（从 0 开始）。
            error:   触发重试的异常。
        """
        await self.session.event_bus.publish(AgentEvent(
            type=EventType.LLM_RETRY,
            agent_id=self.context.agent_id,
            session_id=self.session.id,
            sender_type=SenderType.AGENT,
            payload={"model": self.model, "attempt": attempt + 1, "error": str(error)},
        ))

    # ── 公共入口点 ────────────────────────────────────────────────────────────

    async def run(self, message: str) -> str:
        """
        追加用户消息并执行循环。

        Args:
            message: 用户输入的原始消息。

        Returns:
            Agent 的最终输出字符串。
        """
        # hook: message:inbound:received — 可修改消息内容、注入 additional_context
        hook_result = await self.session.hooks.emit(
            "message:inbound:received",
            {"prompt": message},
            self._build_hook_ctx(),
        )
        # hook 可以替换消息内容
        if hook_result.message is not None:
            message = hook_result.message
        # hook 可以追加额外上下文（拼接到消息末尾，LLM 可见）
        if hook_result.additional_context:
            message = message + "\n\n" + hook_result.additional_context

        if message.startswith("/"):
            await self._handle_command(message)
        else:
            self._append({"role": "user", "content": message})
        return await self._loop()

    async def run_stream(self, message: str):
        """
        追加用户消息并执行循环，返回事件流（AsyncIterator[AgentEvent]）。

        与 run() 的区别：
          - run() 返回最终结果字符串，中间事件通过 self.emitter 推送
          - run_stream() 通过 yield 逐条返回 AgentEvent，调用方直接消费

        实现方式（P2 过渡态）：
          内部临时将 emitter 替换为 BusEmitter，通过 EventBus 订阅收集事件并 yield。
          此方式不需要改动 _loop() 内部逻辑，是向纯 AsyncIterator 演进的第一步。

        注意：
          run_stream() 不支持交互式事件（ask_user / permission_request 会直接返回空/False），
          因此主要用于后台 Agent（子 Agent、teammate）场景。
          根 Agent 的交互式场景仍应使用 run()。

        Args:
            message: 用户输入的原始消息。

        Yields:
            AgentEvent: 逐条事件（token、tool_start、progress、done、error 等）。
        """
        # hook 处理（与 run() 相同）
        hook_result = await self.session.hooks.emit(
            "message:inbound:received",
            {"prompt": message},
            self._build_hook_ctx(),
        )
        if hook_result.message is not None:
            message = hook_result.message
        if hook_result.additional_context:
            message = message + "\n\n" + hook_result.additional_context

        if message.startswith("/"):
            await self._handle_command(message)
        else:
            self._append({"role": "user", "content": message})

        # 直接订阅 self 的事件（self.emitter 已是 BusEmitter，无需再临时替换）
        sub_id = f"stream_{self.context.agent_id[:8]}_{id(self)}"
        filter_fn = lambda e: e.agent_id == self.context.agent_id  # noqa: E731

        # 启动 _loop() 后台任务
        loop_task = asyncio.create_task(self._loop())

        try:
            async with self.session.event_bus.subscribe(sub_id, filter_fn=filter_fn) as sub:
                # 持续从 EventBus 消费事件，直到 _loop() 完成
                while not loop_task.done():
                    try:
                        event = await asyncio.wait_for(sub.get(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    if event is not None:
                        yield event

                # _loop() 完成后，清空订阅队列中剩余的事件
                while True:
                    try:
                        event = await asyncio.wait_for(sub.get(), timeout=0.2)
                    except asyncio.TimeoutError:
                        break
                    if event is None:
                        break
                    yield event

        finally:
            pass  # 无临时状态需要恢复

        # 等待 _loop() 任务完成
        await loop_task

    async def _handle_command(self, raw: str):
        """
        解析 /command [args] 格式的输入，构建 command 消息追加到上下文。

        内置命令（builtin=True）通过 command_registry 查找处理器并执行，
        返回结果作为 stdout 注入消息。非内置命令直接将 command 信息传给 LLM。
        """
        rest = raw[1:]  # 去掉开头的 /
        name, _, args = rest.partition(" ")
        name = name.strip().lower()
        args = args.strip()

        cmd = self.session.commands.get(name)
        stdout = ""

        if cmd and cmd.builtin:
            # 从注册表获取处理器，替代 if/elif 链
            from ccserver.agent.command_registry import get_handler
            handler = get_handler(name)
            if handler:
                stdout = await handler(self, args)

        body = cmd.load_body() if cmd else ""

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

    # ── 派生(委托给 SpawnManager,Step 4 拆出)────────────────────────────────────

    def spawn_child(self, prompt: str, agent_def=None, agent_name=None, prompt_version: str | None = None,
                    model_override: str | None = None, env_vars: dict[str, str] | None = None,
                    agent_id_override: str | None = None) -> "Agent":
        """派生子代理。实现见 SpawnManager.spawn_child;此处保留为公共 API 薄委托。"""
        return self._spawn_manager.spawn_child(
            prompt, agent_def=agent_def, agent_name=agent_name, prompt_version=prompt_version,
            model_override=model_override, env_vars=env_vars, agent_id_override=agent_id_override,
        )

    def spawn_background(self, prompt: str, agent_def=None, agent_name=None, task_id: str = None,
                         model_override: str | None = None, env_vars: dict[str, str] | None = None,
                         agent_id_override: str | None = None, is_teammate: bool = False,
                         is_persistent: bool = False) -> BackgroundAgentHandle:
        """启动后台 Agent。实现见 SpawnManager.spawn_background;此处保留为公共 API 薄委托。"""
        return self._spawn_manager.spawn_background(
            prompt, agent_def=agent_def, agent_name=agent_name, task_id=task_id,
            model_override=model_override, env_vars=env_vars, agent_id_override=agent_id_override,
            is_teammate=is_teammate, is_persistent=is_persistent,
        )

    async def _spawn_teammate(self, team_name: str, name: str, prompt: str, agent_def=None,
                              model_override: str | None = None) -> BackgroundAgentHandle:
        """在 Team 中启动 teammate。实现见 SpawnManager.spawn_teammate;此处委托。"""
        return await self._spawn_manager.spawn_teammate(
            team_name, name, prompt, agent_def=agent_def, model_override=model_override,
        )

    # ── 核心循环 ──────────────────────────────────────────────────────────────

    async def _drain_inbox_and_respond(self) -> tuple[list[dict], bool]:
        """消费 Team inbox 消息。实现见 TeamCoordinator.drain_inbox_and_respond;此处委托。"""
        return await self._team_coordinator.drain_inbox_and_respond()

    async def _loop(self) -> str:
        self.state.start_time = datetime.now(timezone.utc)
        await self._set_phase("running")
        logger.debug("Loop start | agent={} depth={} msgs={} stream={}", self.aid_label, self.context.depth, len(self.context.messages), self.stream)

        # hook: agent:bootstrap — _loop 开始处，可动态裁剪 tools/schemas
        await self.session.hooks.emit_void(
            "agent:bootstrap",
            {"schemas": self._schemas, "tools": list(self.tools.keys())},
            self._build_hook_ctx(),
        )

        round_text = ""
        # 外层 while 支持用户选择"继续"后重入，避免递归调用 _loop()。
        # LimitPolicy 返回 LimitOutcome(continue_loop=True, extra_rounds=N) 时，
        # 本循环增加 round_limit 额度并重置计数器继续执行，否则直接 return。
        while True:
            for round_num in range(self.round_limit):
                self.state.round_num = round_num + 1
                # 处理 inbox 中积压的 Team Mailbox 消息（new_task, shutdown_request, chat 等）
                # 进度事件由本轮末尾主动 publish 到 EventBus，不再依赖外部轮询
                team_messages, shutdown_requested = await self._drain_inbox_and_respond()
                for tm in team_messages:
                    self._append(tm)
                if shutdown_requested:
                    await self._set_phase("done")
                    return round_text + "\n[shutdown by lead]"
                await self._compact_coordinator.maybe_compact()
                # 验证消息序列：修复被外部并发消息打断的 tool_use -> tool_result 对
                # sanitize 是 dict 工具：转 dict 副本处理；若有修复则从结果重建 context.messages
                from ccserver.messages import UnifiedMessage, unified_message_to_wire, wire_to_unified_message
                _wire = [unified_message_to_wire(m) for m in self.context.messages]
                if MessageBuilder.sanitize_messages(_wire):
                    self.context.messages[:] = [wire_to_unified_message(m) for m in _wire]
                    if self.persist:
                        self.session.rewrite_messages(self.context.messages)
                logger.debug("Round {}/{} | agent={}", round_num + 1, self.round_limit, self.aid_label)
                # 调用前快照 messages（转 wire dict，防止后续 append 污染记录）
                input_messages_snapshot = [unified_message_to_wire(m) for m in self.context.messages]

                # ── LLM 一次请求：L2 造消息 → 发遥测 → L1 调模型 → 推 token ──
                await self._set_phase("llm_calling")
                effective_system, effective_messages = await self._message_builder.build()
                # 断言：build() 必须产出非空 messages，否则无法调用 LLM
                assert effective_messages, "MessageBuilder.build() 返回空 messages，无法调用 LLM"

                llm_start_ts = datetime.now(timezone.utc)
                await self.session.event_bus.publish(AgentEvent(
                    type=EventType.LLM_REQUEST,
                    agent_id=self.context.agent_id,
                    session_id=self.session.id,
                    sender_type=SenderType.AGENT,
                    payload={
                        "model": self.model,
                        "message_count": len(effective_messages),
                        "tools_count": len(self._schemas),
                        "system_len": len(effective_system) if effective_system else 0,
                    },
                ))

                try:
                    if self.stream:
                        response = await self._llm_caller.stream(
                            effective_messages,
                            system=effective_system,
                            tools=self._schemas,
                            on_text=self.emitter.emit_token,
                            on_thinking=self.emitter.emit_thinking,
                            on_retry=self._publish_llm_retry,
                        )
                    else:
                        response = await self._llm_caller.invoke(
                            effective_messages,
                            system=effective_system,
                            tools=self._schemas,
                            on_retry=self._publish_llm_retry,
                        )
                except Exception as e:
                    # LLM 永久失败（重试耗尽或不可重试）
                    logger.error("LLM error | agent={} exc_type={} error={}",
                                 self.aid_label, type(e).__name__, e)
                    await self.emitter.emit_error(str(e))
                    await self.session.hooks.emit_void(
                        "prompt:llm:error",
                        {"error": str(e), "model": self.model},
                        self._build_hook_ctx(),
                    )
                    await self._set_phase("error")
                    self.state.last_error = "LLM call failed"
                    await self.session.hooks.emit_void(
                        "agent:stop:failure",
                        {"error": self.state.last_error},
                        self._build_hook_ctx(),
                    )
                    return ""

                # 发布 llm_response 事件（含耗时）
                llm_duration_ms = int((datetime.now(timezone.utc) - llm_start_ts).total_seconds() * 1000)
                # response 是 UnifiedResponse：content 是字符串，tool_calls 是列表
                tool_calls_count = len(response.tool_calls)
                await self.session.event_bus.publish(AgentEvent(
                    type=EventType.LLM_RESPONSE,
                    agent_id=self.context.agent_id,
                    session_id=self.session.id,
                    sender_type=SenderType.AGENT,
                    payload={
                        "model": self.model,
                        "stop_reason": response.stop_reason,
                        "content_blocks_count": tool_calls_count + (1 if response.content else 0),
                        "duration_ms": llm_duration_ms,
                    },
                ))

                # 构建 assistant 消息的 block 列表，写入 context 和 recorder
                from ccserver.messages import UnifiedMessage, UnifiedTextBlock, UnifiedThinkingBlock, UnifiedToolUseBlock
                assistant_blocks = []
                if response.thinking:
                    assistant_blocks.append(UnifiedThinkingBlock(thinking=response.thinking))
                if response.content:
                    assistant_blocks.append(UnifiedTextBlock(text=response.content))
                for tc in response.tool_calls:
                    assistant_blocks.append(UnifiedToolUseBlock(id=tc.id, name=tc.name, input=tc.input))
                # 序列化为 wire dict 供 recorder 使用
                content_for_recorder = [b.to_dict() for b in assistant_blocks]
                self.recorder.record(
                    round_num + 1,
                    input_messages=input_messages_snapshot,
                    response_content=content_for_recorder,
                    stop_reason=response.stop_reason,
                )
                self._append(UnifiedMessage(role="assistant", content=assistant_blocks))

                # ── 非流式分支：直接从 response.content（字符串）累加进 round_text ──
                # 背景：UnifiedResponse.content 现在是字符串，直接累加即可。
                # 流式分支：on_text 回调只推送给 emitter（UI），不写入 round_text。
                # 用 += 累加是为兼容「多轮 tool_use 之间穿插 text」的情况。
                if not self.stream:
                    if response.content:
                        round_text += response.content
                self._last_assistant_time = datetime.now(timezone.utc)
                if round_text:
                    # hook: prompt:llm:output — 每轮 LLM 完成后（observing，纯观测）
                    await self.session.hooks.emit_void(
                        "prompt:llm:output",
                        {"reply": round_text},
                        self._build_hook_ctx(),
                    )

                if response.stop_reason != "tool_use":
                    logger.debug("Loop done  | agent={} rounds={} reply_len={}", self.aid_label, round_num + 1, len(round_text))
                    logger.debug("Loop final_text | agent={} text={!r}", self.aid_label, round_text)

                    # hook: agent:reply:before — LLM 完成回复、发出 done 之前（modifying，可修改最终回复）
                    reply_hook = await self.session.hooks.emit(
                        "agent:reply:before",
                        {"reply": round_text, "round_num": round_num + 1},
                        self._build_hook_ctx(),
                    )
                    if reply_hook.message is not None:
                        round_text = reply_hook.message

                    await self._set_phase("done")
                    # 子代理发 subagent_done，根代理发 done，语义区分
                    if self.context.is_orchestrator:
                        # hook: agent:stop — 根代理最终完成（observing）
                        await self.session.hooks.emit_void(
                            "agent:stop",
                            {"reply": round_text},
                            self._build_hook_ctx(),
                        )
                        await self.emitter.emit_done(round_text)
                    else:
                        await self.emitter.emit_subagent_done(round_text)
                    return round_text

                await self._set_phase("tool_executing")

                # 推送模型：每轮工具调用前主动向 EventBus 广播 PROGRESS 事件。
                # 订阅了此 Agent 事件的父级无需再轮询，直接收到进度更新。
                progress_payload = {
                    "progress": {
                        "round_num": round_num + 1,
                        "max_rounds": self.round_limit,
                        "phase": "tool_executing",
                        "current_tool": self.state.current_tool,
                    }
                }
                # 如果上下文中有 agent_task_id（后台 Agent），带上 task_id 和 status
                # 这样 SSEEmitter/WSEmitter 直接订阅 EventBus 时，可以构造 task_progress 事件
                _agent_task_id = getattr(self.context, "agent_task_id", None)
                if _agent_task_id:
                    progress_payload["task_id"] = _agent_task_id
                    progress_payload["status"] = "running"

                await self.session.event_bus.publish(AgentEvent(
                    type=EventType.PROGRESS,
                    agent_id=self.context.agent_id,
                    session_id=self.session.id,
                    sender_type=SenderType.AGENT,
                    payload=progress_payload,
                ))

                tool_results, trigger_compact = await self._tool_dispatcher.handle(response.tool_calls)

                # 注意：compaction 必须在追加 tool_result 之前执行，
                # 否则 tool_result 会随旧消息一起被压缩丢弃。
                if trigger_compact:
                    await self._compact_coordinator.do_compact(reason="manual compact requested")

                from ccserver.messages import UnifiedMessage
                self._append(UnifiedMessage(role="user", content=tool_results))

                await self._set_phase("running")

            # for 循环耗尽，达到轮次上限
            logger.warning("Round limit reached | agent={} limit={}", self.aid_label, self.round_limit)
            await self._set_phase("limit_reached")
            # 委托给 LimitPolicy 协作者;通过 LimitOutcome 解读控制流(消除回写耦合)
            outcome = await self._limit_policy.handle(round_text)
            if outcome.continue_loop:
                # 用户选择"继续":增加轮次额度并重置计数,重入外层 while
                self.round_limit += outcome.extra_rounds
                self.state.round_num = 0
                await self._set_phase("running")
                continue
            return outcome.final_text

    # ── 工具处理(委托给 ToolDispatcher,Step 5 拆出)──────────────────────────────
    # 保留为薄委托方法:_loop 内部调用 self._tool_dispatcher.handle();
    # 此处 _handle_tools / _handle_agent 维持公共测试契约(被多个 test 直接调用)。

    async def _handle_tools(self, blocks) -> tuple[list[dict], bool]:
        """执行响应中所有 tool_use 块。实现见 ToolDispatcher.handle;此处委托。"""
        return await self._tool_dispatcher.handle(blocks)

    async def _handle_agent(self, task_input: dict) -> "ToolResult":
        """派生子代理并运行。实现见 ToolDispatcher._handle_agent;此处委托。"""
        return await self._tool_dispatcher._handle_agent(task_input)

    # ── 上下文管理 ────────────────────────────────────────────────────────────

    def _append(self, message):
        """向上下文追加消息（统一存 UnifiedMessage），并按配置持久化。

        message 可为 dict 或 UnifiedMessage。经 prompt_engine.on_message（dict-facing）
        处理后，统一转为 UnifiedMessage 存入 context.messages。
        """
        from ccserver.messages import UnifiedMessage, unified_message_to_wire, wire_to_unified_message
        # prompt lib 是 dict-facing：转 dict 进、dict 出
        msg_dict = unified_message_to_wire(message)
        msg_dict = self.prompt_engine.on_message(
            msg_dict, self.session, [unified_message_to_wire(m) for m in self.context.messages],
            skills_override=self.skills_override,
        )
        unified = wire_to_unified_message(msg_dict)
        self.context.messages.append(unified)
        if self.persist:
            self.session.persist_message(unified)

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

