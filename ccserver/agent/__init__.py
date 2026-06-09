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

from ..config import MODEL, MAIN_ROUND_LIMIT, SUB_ROUND_LIMIT, MAX_DEPTH, RECORD_DIR
from ccserver.managers.hooks import HookContext
from ..recorder import Recorder
from ..session import Session
from ..compact import CompactorFactory
from ..compact.tokens import estimate_tokens as _estimate_tokens
from ..utils import get_block_attr, normalize_content_blocks, generate_message_id
from ccserver.builtins.tools import ToolResult
from ccserver.builtins.tools import BuiltinTools
from ccserver.emitters import BaseEmitter
from ccserver.emitters.bus_emitter import BusEmitter
from ..agent_handle import BackgroundAgentHandle
from ..agent_registry import register_handle, unregister_handle
from ..event_bus import AgentEvent, EventType, SenderType
from ..model import ModelAdapter
from .runtime import AgentRuntime  # noqa: F401  Agent 拆分后协作者依赖的运行时契约(Protocol)
from .llm_caller import LLMCaller  # Step 3 拆出:LLM 调用器 + 消息净化

from typing import List, Dict, Any, Optional, Callable

# Agent Team 相关导入（延迟导入避免循环依赖）
from ccserver.team.mailbox import TeamMailbox
from ccserver.team.protocol import (
    MsgType,
    ChatMessage,
)
from ccserver.team.helpers import format_agent_id


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
        stream: bool = True,                        # True=实时 emit token，False=非流式（只返回最终结果）
        env_vars: dict[str, str] | None = None,     # 环境变量，会合并到 context.env_vars
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
        self.stream: bool = stream
        self.state: AgentState = AgentState()
        # 合并环境变量
        if env_vars:
            self.context.env_vars.update(env_vars)
        # run_mode：None 时从 session.settings 读取；子代理强制 "auto"（不允许阻塞等待用户）
        if run_mode is not None:
            self.run_mode: str = run_mode
        else:
            self.run_mode = session.settings.run_mode

        from ccserver.prompt_engine import PromptEngine
        self.prompt_engine: PromptEngine = PromptEngine(prompt_version)
        self.system:List[Dict[str, Any]] = self.prompt_engine.build_system(session, model, language, cch=self.short_aid, injected_system=system, append_system=append_system, is_spawn=is_spawn)

        self.compactor = CompactorFactory.build_default(adapter=adapter, model=model)
        # 上次收到 assistant 消息的时间，供 micro 时间触发压缩使用
        self._last_assistant_time: datetime | None = None
        # 协作者:压缩协调器(Step 2 拆出,Agent 仅持有并委托)
        from .compact_coordinator import CompactCoordinator
        self._compact_coordinator = CompactCoordinator(self, self.compactor)
        # 协作者:LLM 调用器(Step 3 拆出,合并 stream/sync 调用)
        from .llm_caller import LLMCaller
        self._llm_caller = LLMCaller(self)
        # 协作者:派生管理器(Step 4 拆出,负责 spawn_child/background/teammate)
        from .spawn_manager import SpawnManager
        self._spawn_manager = SpawnManager(self)

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
        """
        非阻塞读取 inbox，处理 Agent Team 相关的 mailbox 消息（new_task, shutdown_request, chat 等）。

        进度事件改由 _loop() 每轮主动 publish 到 EventBus（推送模型），
        不再需要外部轮询注入 status_request。

        Returns:
            (需要追加到 messages 的新消息列表, 是否收到 shutdown_request)
        """
        new_messages: list[dict] = []
        shutdown_requested = False

        # 消费 inbox 中的 Team Mailbox 消息
        while True:
            try:
                msg = self.context.inbox.get_nowait()
            except asyncio.QueueEmpty:
                break

            # msg_type 字段标识来自 TeamMailboxPoller 或 EventBus 订阅者的消息
            match msg.get("type") or msg.get("msg_type"):
                case MsgType.NEW_TASK:
                    # 新任务：Team Lead 分配过来的任务，转为 user 消息追加到对话历史
                    new_messages.append({
                        "role": "user",
                        "content": msg.get("task_prompt", msg.get("text", "")),
                        "_ccserver_team_new_task": True,
                        "task_id": msg.get("task_id"),
                    })

                case MsgType.SHUTDOWN_REQUEST:
                    # 关闭请求：Team Lead 要求优雅退出，注入 system 消息让 LLM 总结后结束
                    new_messages.append({
                        "role": "system",
                        "content": "[Team Lead 请求你优雅退出，总结当前进度后结束。]",
                    })
                    shutdown_requested = True

                case MsgType.CHAT:
                    # 聊天消息：来自其他 Agent 的即时通信，附上发送方标识
                    new_messages.append({
                        "role": "user",
                        "content": f"[{msg.get('from_agent')}] {msg.get('text', '')}",
                    })

                case MsgType.PERMISSION_RESPONSE:
                    # 权限响应：由 _wait_permission_response 轮询处理，这里只消费掉避免堆积
                    logger.debug(
                        "Inbox permission_response consumed | agent={} request_id={}",
                        self.aid_label,
                        msg.get("request_id"),
                    )

                case MsgType.CRON_TRIGGER | MsgType.SCHEDULED_TASK_TRIGGER:
                    # 定时任务触发（兼容旧 cron_trigger 和新的 scheduled_task_trigger）
                    cron_prompt = msg.get("prompt", "")
                    new_messages.append({
                        "role": "user",
                        "content": cron_prompt,
                        "_ccserver_scheduled_task": True,
                        "task_id": msg.get("task_id"),
                        "trigger_type": msg.get("trigger_type", "cron"),
                    })
                    logger.debug(
                        "Inbox scheduled_task_trigger consumed | agent={} task_id={} type={}",
                        self.aid_label,
                        msg.get("task_id"),
                        msg.get("trigger_type", "cron"),
                    )

                case _:
                    # 未知消息类型，记录警告但不中断循环
                    logger.warning(
                        "Inbox unknown msg type ignored | agent={} msg={}",
                        self.aid_label,
                        msg,
                    )

        return new_messages, shutdown_requested

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
                if LLMCaller.sanitize_messages(self.context.messages):
                    if self.persist:
                        self.session.rewrite_messages(self.context.messages)
                logger.debug("Round {}/{} | agent={}", round_num + 1, self.round_limit, self.aid_label)
                # 调用前快照 messages（深拷贝，防止后续 append 污染记录）
                input_messages_snapshot = [dict(m) for m in self.context.messages]

                # 委托 LLMCaller(合并了 stream/sync 调用)
                response = await self._llm_caller.call(stream=self.stream)

                if response is None:
                    # LLM 永久失败（重试耗尽）
                    await self._set_phase("error")
                    self.state.last_error = "LLM call failed after retries"
                    await self.session.hooks.emit_void(
                        "agent:stop:failure",
                        {"error": self.state.last_error},
                        self._build_hook_ctx(),
                    )
                    return ""

                content = normalize_content_blocks(response.content)
                self.recorder.record(
                    round_num + 1,
                    input_messages=input_messages_snapshot,
                    response_content=content,
                    stop_reason=response.stop_reason,
                )
                self._append({"role": "assistant", "content": content})
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

                tool_results, trigger_compact = await self._handle_tools(response.content)

                # 注意：compaction 必须在追加 tool_result 之前执行，
                # 否则 tool_result 会随旧消息一起被压缩丢弃。
                if trigger_compact:
                    await self._compact_coordinator.do_compact(reason="manual compact requested")

                self._append({"role": "user", "content": tool_results})

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

        # 扫描是否有多个 Agent 工具调用，决定是否启用并行模式
        _agent_count = 0
        for _block in blocks:
            if get_block_attr(_block, "type") == "tool_use":
                _name = get_block_attr(_block, "name") or ""
                if _name == "Agent":
                    _agent_count += 1
        parallel_agent_mode = _agent_count > 1

        # 收集 Agent 工具的异步调用信息（仅在并行模式下使用）
        # 每个元素: (results_index, block_id, effective_input, preview, tool_start_ts, task)
        agent_tasks: list[tuple[int, str, dict, str, datetime, asyncio.Task]] = []

        for block in blocks:
            if get_block_attr(block, "type") != "tool_use":
                continue

            name: str = get_block_attr(block, "name") or ""
            input_: dict = get_block_attr(block, "input") or {}
            block_id: str = get_block_attr(block, "id") or ""

            # ── 运行时权限检查 ────────────────────────────────────────────────
            if name in ask_tools:
                # hook: tool:permission:request — modifying，可阻断或修改决策
                perm_hook = await self.session.hooks.emit(
                    "tool:permission:request",
                    {"tool_name": name, "tool_input": input_, "tool_use_id": block_id},
                    self._build_hook_ctx(),
                )

                # block 保持向后兼容（等价于 deny）
                if perm_hook.block:
                    logger.info("Hook blocked permission | agent={} tool={} reason={}", self.aid_label, name, perm_hook.block_reason)
                    result = ToolResult.error(perm_hook.block_reason or f"Tool '{name}' blocked by permission hook.")
                    results.append(result.to_api_dict(block_id))
                    continue

                behavior = perm_hook.permission_behavior
                if behavior == "allow":
                    # hook 显式允许，跳过权限询问直接执行
                    logger.info("Hook allowed permission | agent={} tool={}", self.aid_label, name)
                    # 继续走下方的 tool:call:before 逻辑
                elif behavior in ("deny",):
                    # deny（已被 block 捕获，此处为兜底）
                    logger.info("Hook denied permission | agent={} tool={}", self.aid_label, name)
                    result = ToolResult.error(f"Tool '{name}' denied by permission hook.")
                    results.append(result.to_api_dict(block_id))
                    continue
                elif behavior in ("ask", "passthrough"):
                    # 继续走原有权限逻辑：interactive 弹窗 / auto 拒绝
                    if self.run_mode == "interactive":
                        logger.info("Permission request | agent={} tool={} mode=interactive", self.aid_label, name)
                        granted = await self.emitter.emit_permission_request(name, input_)
                        if not granted:
                            logger.info("Permission denied  | agent={} tool={}", self.aid_label, name)
                            await self.session.hooks.emit_void(
                                "tool:permission:denied",
                                {"tool_name": name, "tool_input": input_, "tool_use_id": block_id, "reason": "user_denied"},
                                self._build_hook_ctx(),
                            )
                            result = ToolResult.error(f"Tool '{name}' was denied by user.")
                            results.append(result.to_api_dict(block_id))
                            continue
                        logger.info("Permission granted | agent={} tool={}", self.aid_label, name)
                    else:
                        logger.info("Permission denied (auto) | agent={} tool={}", self.aid_label, name)
                        await self.session.hooks.emit_void(
                            "tool:permission:denied",
                            {"tool_name": name, "tool_input": input_, "tool_use_id": block_id, "reason": "auto_mode"},
                            self._build_hook_ctx(),
                        )
                        result = ToolResult.error(
                            f"Tool '{name}' requires user confirmation but run_mode is 'auto'. "
                            "Add it to permissions.ask and use interactive mode, or remove it from ask_tools."
                        )
                        results.append(result.to_api_dict(block_id))
                        continue

            # hook: tool:call:before — 工具执行前（modifying，可阻断、可修改输入）
            tool_hook = await self.session.hooks.emit(
                "tool:call:before",
                {"tool_name": name, "tool_input": input_, "tool_use_id": block_id},
                self._build_hook_ctx(),
            )
            if tool_hook.block:
                logger.info("Hook blocked tool | agent={} tool={} reason={}", self.aid_label, name, tool_hook.block_reason)
                result = ToolResult.error(tool_hook.block_reason or f"Tool '{name}' blocked by hook.")
                results.append(result.to_api_dict(block_id))
                continue
            # hook 可以修改工具输入（updated_input）
            if tool_hook.updated_input is not None:
                logger.debug("Hook updated tool input | agent={} tool={}", self.aid_label, name)
                input_ = tool_hook.updated_input

            if name.startswith("mcp__"):
                # MCP 工具：显示所有参数，每个 key=value 一项，value 截断 200 字符
                preview_parts = [f"{k}={str(v)[:200]}" for k, v in input_.items()]
                preview = ", ".join(preview_parts)
            else:
                preview = str(list(input_.values())[0])[:80] if input_ else ""

            # 记录工具执行开始时间，用于计算耗时
            tool_start_ts = datetime.now(timezone.utc)
            self.state.current_tool = name

            await self.emitter.emit_tool_start(name, preview)

            if name == "Agent":
                if parallel_agent_mode:
                    # 并行模式：创建 task 但不立即 await，循环结束后统一 gather
                    task = asyncio.create_task(self._handle_agent(input_))
                    agent_tasks.append((len(results), block_id, input_, preview, tool_start_ts, task))
                    results.append(None)  # 预占位，后面替换为实际结果
                    continue
                result = await self._handle_agent(input_)
            elif name == "SendMessage":
                result = await self._handle_send_message(input_)
            elif name == "AskUserQuestion":
                result = await self._handle_ask_user(input_)
            elif name == "Compact":
                trigger_compact = True
                result = ToolResult.ok("Compressing...")
            elif name.startswith("mcp__"):
                result = await self._handle_mcp_tool(name, input_, block_id)
            else:
                tool = self.tools.get(name)
                if tool:
                    logger.debug("Tool call  | agent={} tool={} input={}", self.aid_label, name, input_)
                    result = await tool(**input_)
                    logger.debug("Tool result| agent={} tool={} result={!r}", self.aid_label, name, result.content_text[:200] if result.content_text else "")
                else:
                    logger.warning("Unknown tool | agent={} tool={}", self.aid_label, name)
                    result = ToolResult.error(f"Unknown tool: {name}")

            tool_duration_ms = int((datetime.now(timezone.utc) - tool_start_ts).total_seconds() * 1000)
            self.state.current_tool = None

            # ── 多模态图像路由（NATIVE vs TRANSCRIBE）────────────────────────
            # 若工具结果含图像，根据 adapter 能力决定处理路径：
            #   NATIVE:     主模型支持图像 AND endpoint 支持 image block in tool_result
            #               → 直接把图像放进 tool_result 发给主模型
            #   TRANSCRIBE: 主模型不支持图像 OR endpoint 不支持 image block in tool_result
            #               → 调用 VLM 将图像转为文字描述，再放进 tool_result
            if result.has_image:
                can_native = (
                    self.adapter.supports_image
                    and self.adapter.supports_image_in_tool_result
                )
                if not can_native:
                    # TRANSCRIBE 路径：用 VLM 将图像描述为文字，替换原始 result
                    result = await self._transcribe_image_result(result, name)

            # 多模态结果（含图像）走 emit_tool_result_with_image，普通结果走原路径
            if result.has_image:
                await self.emitter.emit_tool_result_with_image(name, result)
            else:
                await self.emitter.emit_tool_result(name, result.content_text)

            # 发布详细的 tool_done 事件到 EventBus，供 monitor 展示工具调用详情
            await self.session.event_bus.publish(AgentEvent(
                type=EventType.TOOL_DONE,
                agent_id=self.context.agent_id,
                session_id=self.session.id,
                sender_type=SenderType.AGENT,
                payload={
                    "tool_name": name,
                    "tool_use_id": block_id,
                    "duration_ms": tool_duration_ms,
                    "is_error": result.is_error,
                    "result_preview": result.content_text[:200],
                    "tool_input_preview": preview,
                    "has_image": result.has_image,
                },
            ))
            # hook: tool:call:after / tool:call:failure（observing）
            if result.is_error:
                await self.session.hooks.emit_void(
                    "tool:call:failure",
                    {"tool_name": name, "tool_use_id": block_id, "tool_input": input_, "error": result.content_text or ""},
                    self._build_hook_ctx(),
                )
            else:
                await self.session.hooks.emit_void(
                    "tool:call:after",
                    {"tool_name": name, "tool_use_id": block_id, "tool_input": input_, "tool_response": result.content_text or ""},
                    self._build_hook_ctx(),
                )
            # hook: tool:result:persist — 工具结果持久化前（observing，适合审计/记录）
            await self.session.hooks.emit_void(
                "tool:result:persist",
                {
                    "tool_name": name,
                    "tool_use_id": block_id,
                    "tool_input": input_,
                    "is_error": result.is_error,
                    "result_text": result.content_text or "",
                    "has_image": result.has_image,
                },
                self._build_hook_ctx(),
            )
            results.append(result.to_api_dict(block_id))

        # ── 并行等待所有 Agent 工具完成（仅当存在多个 Agent 调用时）──
        if agent_tasks:
            if len(agent_tasks) > 1:
                agent_results = await asyncio.gather(
                    *[task for _, _, _, _, _, task in agent_tasks],
                    return_exceptions=True,
                )
            else:
                # 只有一个 Agent 时直接 await（避免不必要的 gather 开销）
                _, _, _, _, _, task = agent_tasks[0]
                try:
                    agent_results = [await task]
                except Exception as e:
                    agent_results = [e]

            for (idx, bid, inp, preview, ts, _), res in zip(agent_tasks, agent_results):
                if isinstance(res, Exception):
                    logger.error("Agent tool parallel execution failed | error={}", res)
                    result = ToolResult.error(str(res))
                else:
                    result = res

                tool_duration_ms = int((datetime.now(timezone.utc) - ts).total_seconds() * 1000)
                self.state.current_tool = None

                await self.emitter.emit_tool_result("Agent", result.content_text)

                await self.session.event_bus.publish(AgentEvent(
                    type=EventType.TOOL_DONE,
                    agent_id=self.context.agent_id,
                    session_id=self.session.id,
                    sender_type=SenderType.AGENT,
                    payload={
                        "tool_name": "Agent",
                        "tool_use_id": bid,
                        "duration_ms": tool_duration_ms,
                        "is_error": result.is_error,
                        "result_preview": result.content_text[:200],
                        "tool_input_preview": preview,
                    },
                ))

                if result.is_error:
                    await self.session.hooks.emit_void(
                        "tool:call:failure",
                        {"tool_name": "Agent", "tool_use_id": bid, "tool_input": inp, "error": result.content_text or ""},
                        self._build_hook_ctx(),
                    )
                else:
                    await self.session.hooks.emit_void(
                        "tool:call:after",
                        {"tool_name": "Agent", "tool_use_id": bid, "tool_input": inp, "tool_response": result.content_text or ""},
                        self._build_hook_ctx(),
                    )

                results[idx] = result.to_api_dict(bid)

        return results, trigger_compact

    async def _transcribe_image_result(self, result: "ToolResult", tool_name: str) -> "ToolResult":
        """
        TRANSCRIBE 路径：将图像 tool_result 中的图像转换为文字描述。

        当主模型不支持图像（supports_image=False）或 endpoint 不支持图像 tool_result
        （supports_image_in_tool_result=False）时调用。

        使用 VLMRouter 选择最佳视觉模型进行描述，若 VLM 不可用则返回占位文字。

        Args:
            result:    包含图像 block 的 ToolResult（has_image=True）
            tool_name: 工具名称（用于日志）

        Returns:
            将图像替换为文字描述的新 ToolResult（不含 image block）
        """
        from ccserver.model.routing.router import VLMRouter
        from ccserver.model.media.describe import describe_image_with_model

        image_base64 = result.get_image_base64()
        if not image_base64:
            # 无法提取图像数据，保留原始文字部分
            logger.warning("TRANSCRIBE: 无法提取图像数据 | tool={}", tool_name)
            return ToolResult.ok(result.content_text or "[图像无法显示]")

        try:
            # 使用 VLMRouter 选择最佳视觉模型
            router = VLMRouter(
                main_model=self.model,
                main_adapter=self.adapter,
            )
            route = await router.route()

            # VLMRouter.route() 在主模型支持图像时返回 native，但此处已知不走 native
            # 因此强制选取 transcribe 路径的 VLM adapter
            if route.is_native:
                # 极端情况：VLMRouter 认为主模型可以看图，但 compat 说不支持 image in tool_result
                # 此时用主模型的 adapter 做描述（主模型能看图，只是 tool_result 限制）
                vlm_adapter = route.adapter
                vlm_model = route.model
            else:
                vlm_adapter = route.adapter
                vlm_model = route.model

            logger.info(
                "TRANSCRIBE 图像描述 | tool={} vlm_model={} vlm_provider={}",
                tool_name, vlm_model, route.provider_id,
            )

            description = await describe_image_with_model(
                image_base64=image_base64,
                adapter=vlm_adapter,
                model=vlm_model,
                max_tokens=1000,
            )

            # 保留原始文字部分（如截图尺寸说明），拼接图像描述
            existing_text = result.content_text
            if existing_text and existing_text != "[multimodal content]":
                combined = f"{existing_text}\n\n[图像内容描述]\n{description}"
            else:
                combined = f"[图像内容描述]\n{description}"

            return ToolResult.ok(combined)

        except Exception as e:
            # VLM 调用失败时降级为占位文字，不阻断主流程
            logger.warning("TRANSCRIBE 失败，使用占位文字 | tool={} error={}", tool_name, e)
            existing_text = result.content_text
            return ToolResult.ok(existing_text or "[图像无法显示：VLM 不可用]")

    async def _handle_agent(self, task_input: dict) -> ToolResult:
        """
        派生子代理并运行。

        根据 run_in_background 参数分为两条路径：
          - run_in_background=False（默认）：await child._loop() 阻塞等待，返回摘要。
          - run_in_background=True        ：调用 spawn_background() 立即返回 task_id，
                                           主 Agent 继续运行，后台 Agent 异步执行。
        """
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
        # agent_name 优先使用 subagent_type（如 "web-search"），description 是任务描述（如 "Search for papers"）
        subagent_type = task_input.get("subagent_type", "")
        agent_name = subagent_type or task_input.get("description", "")
        model_override = task_input.get("model", "") or None
        run_in_background = bool(task_input.get("run_in_background", False))
        team_name = task_input.get("team_name", "")
        teammate_name = task_input.get("name", "")
        logger.info(
            "Agent tool called  | parent={} subagent_type={} description={} "
            "model={} run_in_background={} team_name={} teammate_name={}",
            self.aid_label, subagent_type or "(generic)", agent_name or "-",
            model_override or "inherit", run_in_background,
            team_name or "-", teammate_name or "-",
        )
        agent_def = self.session.agents.get(subagent_type) if subagent_type else None
        if subagent_type and agent_def is None:
            logger.warning("Agent def not found | subagent_type={}", subagent_type)

        # is_persistent 决策：工具参数 > AgentDef 配置 > 默认 False（LLM 自动派生默认临时）
        _persistent_param = task_input.get("persistent")
        if _persistent_param is not None:
            is_persistent = bool(_persistent_param)
        elif agent_def is not None:
            is_persistent = agent_def.is_persistent
        else:
            is_persistent = False

        # ── Team 分支 ──
        if team_name and teammate_name and self.session.settings.user_agent_team:
            if agent_def and not agent_def.is_team_capable:
                return ToolResult.error(
                    f"Agent '{subagent_type}' is not team-capable. "
                    f"Set is_team_capable=true in its frontmatter."
                )
            try:
                handle = await self._spawn_teammate(
                    team_name=team_name,
                    name=teammate_name,
                    prompt=prompt,
                    agent_def=agent_def,
                    model_override=model_override,
                )
            except Exception as e:
                logger.error("Spawn teammate failed | error={}", e)
                return ToolResult.error(f"Failed to spawn teammate: {e}")
            return ToolResult.ok(
                f"Teammate '{teammate_name}' spawned in team '{team_name}' (task_id={handle.agent_task_id})"
            )

        # hook: subagent:spawning — 子代理即将启动（observing）
        await self.session.hooks.emit_void(
            "subagent:spawning",
            {},
            # 此时 child 尚未创建，传入父级 context
            {"agent_id": self.context.agent_id, "depth": self.context.depth},
        )

        # ── 后台模式：spawn_background() 立即返回 ───────────────────────────
        if run_in_background:
            handle = self.spawn_background(
                prompt=prompt,
                agent_def=agent_def,
                agent_name=agent_name,
                model_override=model_override,
                task_id=None,
                is_persistent=is_persistent,
            )
            logger.info(
                "Agent background  | parent={} agent_task_id={} agent_id={}",
                self.aid_label, handle.agent_task_id, handle.agent_id[:8]
            )
            # hook 已在 spawn_background 内部触发
            return ToolResult.ok(
                f"Agent started in background (agent_task_id={handle.agent_task_id})"
            )

        # ── 同步模式：spawn_child + _loop() 阻塞等待 ───────────────────────
        child = self.spawn_child(
            prompt, agent_def=agent_def, agent_name=agent_name, model_override=model_override
        )
        agent_type_label = (
            f"{subagent_type}(defined)" if agent_def
            else f"{subagent_type}(undefined)" if subagent_type
            else "(generic)"
        )
        logger.info(
            "Child agent spawned | parent={} child={} depth={} type={}",
            self.aid_label, child.aid_label, child.context.depth, agent_type_label
        )
        # hook: subagent:spawned — 子代理已创建，即将运行（observing）
        await self.session.hooks.emit_void(
            "subagent:spawned",
            {"subagent_id": child.context.agent_id, "subagent_name": child.context.name or ""},
            child._build_hook_ctx(),
        )

        # 注册到 session.agent_tasks，使 monitor 能追踪同步子 Agent
        from ccserver.tasks import AgentTaskState, generate_agent_id, AgentTaskStatus
        sync_task_id = generate_agent_id()
        sync_task = AgentTaskState(
            id=sync_task_id,
            agent_id=child.context.agent_id,
            agent_name=child.context.name or "unnamed",
            description=f"[Agent] {child.context.name or 'unnamed'}: {prompt[:80]}",
            prompt=prompt,
            parent_id=child.context.parent_id,
            is_persistent=is_persistent,
        )
        child.context.agent_task_id = sync_task_id
        self.session.agent_tasks.register(sync_task)
        logger.info(
            "Sync agent registered | parent={} agent_task_id={} agent_id={}",
            self.aid_label, sync_task_id, child.context.agent_id[:8]
        )

        try:
            sync_task.status = AgentTaskStatus.RUNNING
            sync_task.start_time = datetime.now(timezone.utc)
            summary = await child._loop()
            sync_task.status = AgentTaskStatus.COMPLETED
            sync_task.result = summary
        except Exception as e:
            sync_task.status = AgentTaskStatus.FAILED
            sync_task.error = str(e)
            raise
        finally:
            sync_task.end_time = datetime.now(timezone.utc)
            if not sync_task.is_persistent:
                self.session.agent_tasks.evict(sync_task_id)

        logger.info(
            "Child agent done   | child={} summary_len={}",
            child.aid_label, len(summary)
        )
        # hook: subagent:ended — 子代理完成（observing）
        await self.session.hooks.emit_void(
            "subagent:ended",
            {"summary": summary, "subagent_id": child.context.agent_id},
            child._build_hook_ctx(),
        )
        return ToolResult.ok(summary or "(no summary)")

    async def _handle_send_message(self, input_: dict) -> ToolResult:
        """
        处理 SendMessage 工具调用，将消息写入目标 teammate 的 Mailbox。

        仅当当前 Agent 属于某个已激活的 Team 时才允许调用。
        """
        to = input_.get("to", "")
        message = input_.get("message", "")
        summary = input_.get("summary", "")

        if not to or not message:
            return ToolResult.error("SendMessage requires 'to' and 'message' parameters.")

        # 检查当前 agent 是否属于某个 team
        team_name = getattr(self, "_team_name", None)
        if not team_name:
            return ToolResult.error(
                "SendMessage is only available for agents running inside a team."
            )

        registry = self.session.team_registry
        if registry is None:
            return ToolResult.error("Team feature is not enabled.")

        team = registry.get_team(team_name)
        if team is None:
            return ToolResult.error(f"Team '{team_name}' not found.")

        mailbox = TeamMailbox(team_name, self.session.storage)
        from_agent = self.context.agent_id

        if to == "*":
            # 广播给所有成员（排除自己）
            recipients = [m.agent_id for m in team.members.values() if m.agent_id != from_agent]
            chat_msg = ChatMessage(
                from_agent=from_agent,
                to_agent="*",
                text=message,
                summary=summary or None,
            )
            await mailbox.broadcast(chat_msg, recipients=recipients, exclude=from_agent)
            logger.info(
                "SendMessage broadcast | agent={} team={} recipients={}",
                self.aid_label, team_name, len(recipients)
            )
            return ToolResult.ok(f"Message broadcast to {len(recipients)} teammate(s).")
        else:
            # 单播给指定成员
            to_agent = format_agent_id(to, team_name)
            if to_agent not in team.members:
                return ToolResult.error(f"Teammate '{to}' not found in team '{team_name}'.")
            chat_msg = ChatMessage(
                from_agent=from_agent,
                to_agent=to_agent,
                text=message,
                summary=summary or None,
            )
            await mailbox.send(chat_msg)
            logger.info(
                "SendMessage sent | agent={} team={} to={}",
                self.aid_label, team_name, to_agent
            )
            return ToolResult.ok(f"Message sent to {to}.")

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

    async def _handle_mcp_tool(self, name: str, input_: dict, block_id: str) -> ToolResult:
        """转发 mcp__<server>__<tool> 调用到对应的 MCP server。"""
        parts = name.split("__", 2)
        if len(parts) != 3:
            return ToolResult.error(f"Invalid MCP tool name: {name}")
        _, server_name, tool_name = parts
        client = self.session.mcp.get_client(server_name)
        if client is None:
            return ToolResult.error(f"MCP server not found: {server_name}")
        logger.debug("MCP call | agent={} server={} tool={} input={}", self.aid_label, server_name, tool_name, input_)
        outcome = await client.call(tool_name, input_)
        # client.call() 返回 MCPOutcome，用 is_error 判断，不再依赖字符串前缀
        if outcome.is_error:
            logger.error(
                "MCP tool failed | agent={} server={} tool={} error={}",
                self.aid_label, server_name, tool_name, outcome.content,
            )
            # mcp:call:failure — MCP 工具调用失败，payload 额外带 server_name
            await self.session.hooks.emit_void(
                "mcp:call:failure",
                {
                    "server": server_name,
                    "tool": tool_name,
                    "tool_use_id": block_id,
                    "tool_input": input_,
                    "error": outcome.content,
                },
                self._build_hook_ctx(),
            )
            return ToolResult.error(outcome.content)
        return ToolResult.ok(outcome.content)

    # ── 上下文管理 ────────────────────────────────────────────────────────────

    def _append(self, message: dict):
        """
        向上下文追加消息，并根据配置决定是否持久化。

        始终将消息写入 context.messages（对根代理来说，这与 session.messages 是同一对象）。
        persist=True 时额外调用 session.persist_message 将消息写入磁盘。

        消息在写入前统一经过 lib.on_message() 处理（包装格式、注入 reminder 等）。
        """
        message = self.prompt_engine.on_message(
            message, self.session, self.context.messages,
            skills_override=self.skills_override,
        )

        # 始终写入 context.messages
        self.context.messages.append(message)
        # persist=True 时额外写磁盘（只写盘，不重复 append 到列表）
        if self.persist:
            self.session.persist_message(message)

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

