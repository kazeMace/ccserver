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
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from loguru import logger

from .config import MODEL, MAIN_ROUND_LIMIT, SUB_ROUND_LIMIT, MAX_DEPTH, RECORD_DIR
from ccserver.managers.hooks import HookContext
from .recorder import Recorder
from .session import Session
from .compactor import Compactor
from .utils import get_block_attr, normalize_content_blocks, generate_message_id
from ccserver.builtins.tools import ToolResult
from ccserver.builtins.tools import BuiltinTools
from ccserver.emitters import BaseEmitter
from ccserver.emitters import FilterEmitter
from ccserver.emitters.bus_emitter import BusEmitter
from .agent_handle import BackgroundAgentHandle
from .agent_registry import register_handle, unregister_handle
from .event_bus import AgentEvent, EventType, SenderType
from .model import ModelAdapter, get_adapter

from typing import List, Dict, Any, Optional, Callable

# Agent Team 相关导入（延迟导入避免循环依赖）
from ccserver.team.mailbox import TeamMailbox
from ccserver.team.protocol import (
    MsgType,
    TeamMessage,
    NewTaskMessage,
    ShutdownRequestMessage,
    PermissionRequestMessage,
    PermissionResponseMessage,
    ChatMessage,
    IdleNotificationMessage,
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

        # 保存原始 emitter
        original_emitter = self.emitter

        # 临时替换为 BusEmitter，事件会自动 publish 到 EventBus
        bus_emitter = BusEmitter(
            bus=self.session.event_bus,
            agent_id=self.context.agent_id,
            session_id=self.session.id,
        )
        self.emitter = bus_emitter

        # 订阅自己的事件
        sub_id = f"stream_{self.context.agent_id[:8]}_{id(self)}"
        filter_fn = lambda e: e.agent_id == self.context.agent_id

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
            # 恢复原始 emitter
            self.emitter = original_emitter

        # 等待 _loop() 任务完成，获取最终结果
        result = await loop_task

        # 兜底：如果 _loop() 正常返回但没有发布 DONE 事件（如 report 策略），
        # 在这里补发一个 DONE 事件，确保调用方收到终止信号
        # 注：正常情况下 _finish_with_last_text / emit_done 会发布 DONE 事件
        if result and not loop_task.cancelled():
            # 检查是否已发过 DONE——由于上面的订阅已经关闭，无法直接检查
            # 简单处理：如果 result 有内容，说明任务已完成，无需重复 yield
            pass

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

    def spawn_child(self, prompt: str, agent_def=None, agent_name=None, prompt_version: str | None = None, model_override: str | None = None, env_vars: dict[str, str] | None = None, agent_id_override: str | None = None) -> "Agent":
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
        from ccserver.builtins.tools.constants import CHILD_DISALLOWED_TOOLS, CHILD_DEFAULT_TOOLS, TEAMMATE_EXTRA_TOOLS

        logger.debug(
            "spawn_child called | "
            "agent_name_param={!r} agent_def={} agent_def_name={!r}",
            agent_name,
            agent_def is not None,
            getattr(agent_def, "name", None) if agent_def else None,
        )

        # hook: subagent:spawn:before — 子代理派生前（observing，用于审计/记录）
        # spawn_child 是同步方法，使用 create_task 异步发布 hook，不阻塞主流程
        try:
            asyncio.get_running_loop()
            asyncio.create_task(self.session.hooks.emit_void(
                "subagent:spawn:before",
                {
                    "prompt_preview": prompt[:200],
                    "agent_name": agent_name,
                    "agent_def": getattr(agent_def, "name", None) if agent_def else None,
                    "depth": self.context.depth + 1,
                },
                self._build_hook_ctx(),
            ))
        except RuntimeError:
            pass  # 无事件循环时（如单元测试）静默跳过

        # ── skills：子代理默认无 skill catalog，除非 agent_def.skills 显式指定 ──
        if agent_def is not None and agent_def.skills is not None:
            child_skills_override = agent_def.skills   # list[str]，可能是空列表
        else:
            child_skills_override = []                 # 不注入任何 skill catalog

        # 子代理的初始消息也要经过 prompt_engine.on_message() 处理
        initial_message = self.prompt_engine.on_message(
            {"role": "user", "content": prompt}, self.session, [],
            skills_override=child_skills_override,
        )
        # 子代理继承父代理的环境变量
        child_env_vars = dict(self.context.env_vars)
        # agent_name 优先级：显式传入 > agent_def.name > None
        effective_name = agent_name or (agent_def.name if agent_def else None)
        logger.debug(
            "effective_name={!r} | agent_name_param={!r} agent_def_name={!r}",
            effective_name, agent_name, getattr(agent_def, "name", None) if agent_def else None,
        )
        child_context = AgentContext(
            name=effective_name,
            messages=[initial_message],
            depth=self.context.depth + 1,
            parent_id=self.context.agent_id,
            parent_name=self.context.name,
            env_vars=child_env_vars,
        )
        if agent_id_override:
            child_context.agent_id = agent_id_override

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
        # 优先级：model_override > agent_def.model > agent_def.model_hint > 继承父 agent 的 model
        child_model = model_override
        if not child_model and agent_def:
            if agent_def.model:
                child_model = agent_def.model
            elif agent_def.model_hint:
                # 解析 model_hint 为具体模型名
                child_model = self._resolve_model_hint(agent_def.model_hint)
        if not child_model:
            child_model = self.model

        # emitter：子 agent 默认屏蔽 token 流（stream=False），避免子 agent 的思考过程
        # 直接透传到客户端与父 agent 输出混在一起。
        # agent_def.output_mode 映射到 verbosity 参数：
        #   None / "verbose"    → verbosity="verbose"（透传工具事件，但不流 token）
        #   "final_only"        → verbosity="final_only"（只透传 done/error）
        verbosity = (agent_def.output_mode if agent_def and agent_def.output_mode else "verbose")
        child_emitter = FilterEmitter(self.emitter, verbosity=verbosity, stream=False, interactive=False)

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
            env_vars=env_vars,
        )

        # ── MCP schemas 过滤后追加（子代理 MCP 必须显式指定，agent_def.mcp is None 则无 MCP）──
        if agent_def is not None and agent_def.mcp is not None:
            allowed_mcp = set(agent_def.mcp)
            child._schemas += [s for s in self.session.mcp.schemas() if s["name"] in allowed_mcp]

        # 让 prompt_engine 对 schema 描述做后处理（如 cc_reverse 替换为 CC 原版描述）
        child._schemas = child.prompt_engine.patch_tool_schemas(child._schemas)

        child.recorder.schemas = child._schemas

        logger.debug(
            "spawn_child done | child_name={!r} child_aid={} child_context_name={!r} parent={}",
            child.context.name,
            child.aid_label,
            child_context.name,
            self.aid_label,
        )

        # 发布 subagent_spawned 事件，供 monitor 追踪 Agent 树形关系
        # spawn_child 是同步方法，使用 create_task 异步发布事件
        # 无事件循环时（如单元测试）静默跳过，避免 RuntimeError
        try:
            asyncio.get_running_loop()
            asyncio.create_task(self.session.event_bus.publish(AgentEvent(
                type=EventType.SUBAGENT_SPAWNED,
                agent_id=self.context.agent_id,
                session_id=self.session.id,
                sender_type=SenderType.AGENT,
                payload={
                    "parent_id": self.context.agent_id,
                    "child_id": child.context.agent_id,
                    "child_name": child.context.name or "unnamed",
                    "depth": child.context.depth,
                    "mode": "sync",
                    "model": child.model,
                    "round_limit": child.round_limit,
                },
            )))
        except RuntimeError:
            pass  # no running event loop — skip event publishing

        return child

    @staticmethod
    def _resolve_model_hint(hint: str) -> str | None:
        """
        将 model_hint 快捷方式解析为具体模型名。

        支持的 hint：
          "haiku"   → claude-haiku-4-5-20251001（Anthropic Haiku）
          "sonnet"  → claude-sonnet-4-6（当前默认 Sonnet）
          "opus"    → claude-opus-4-7（Anthropic Opus）
          "inherit" → None（由调用方使用父模型）

        不支持的 hint 返回 None，由调用方 fallback 到父模型。

        Args:
            hint: model_hint 字符串

        Returns:
            具体模型名，或 None
        """
        from .config import MODEL
        _HINT_MAP = {
            "haiku": "claude-haiku-4-5-20251001",
            "sonnet": MODEL,          # 跟随全局默认模型
            "opus": "claude-opus-4-7",
            "inherit": None,          # 由调用方使用 self.model
        }
        return _HINT_MAP.get(hint.lower().strip())

# ── 父 Agent 通知 ───────────────────────────────────────────────────────
# 已由 _watch_terminal_events（EventBus 订阅者）替代，收到终端事件时直接注入
# 父 Agent messages，见 spawn_background() 中 _watch_terminal_events 闭包。

    def spawn_background(
        self,
        prompt: str,
        agent_def=None,
        agent_name=None,
        task_id: str = None,
        model_override: str | None = None,
        env_vars: dict[str, str] | None = None,
        agent_id_override: str | None = None,
        is_teammate: bool = False,
        is_persistent: bool = False,
    ) -> BackgroundAgentHandle:
        """
        启动后台 Agent（非阻塞）。

        返回 BackgroundAgentHandle，外部可通过 handle 查询状态、发送消息、获取结果。
        同时通过 self.emitter（父级 SSE/WebSocket emitter）推送：
          - task_started：Agent 刚启动
          - task_done：Agent 结束（completed / failed / cancelled）
        """
        # 0. 生成 Agent 任务 ID（"a" + uuid[:8]）
        from ccserver.tasks import AgentTaskState, generate_agent_id
        agent_task_id = generate_agent_id()

        # 1. 创建子 Agent（后台不需要实时流式）
        child = self.spawn_child(
            prompt=prompt,
            agent_def=agent_def,
            agent_name=agent_name,
            model_override=model_override,
            env_vars=env_vars,
            agent_id_override=agent_id_override,
        )

        # 2. 创建 AgentTaskState 并注册到 Session
        #    注意：子 Agent 的事件发布由 run_stream() 内部统一处理（临时替换为 BusEmitter），
        #    不再需要在此处手动替换 child.emitter。
        resolved_name = agent_name or child.context.name or "unnamed"
        logger.debug(
            "spawn_background | resolved_name={!r} agent_name_param={!r} child.context.name={!r} task_id={}",
            resolved_name, agent_name, child.context.name, agent_task_id,
        )

        # 发布 subagent_spawned 事件，供 monitor 追踪 Agent 树形关系
        asyncio.create_task(self.session.event_bus.publish(AgentEvent(
            type=EventType.SUBAGENT_SPAWNED,
            agent_id=self.context.agent_id,
            session_id=self.session.id,
            sender_type=SenderType.AGENT,
            payload={
                "parent_id": self.context.agent_id,
                "child_id": child.context.agent_id,
                "child_name": child.context.name or "unnamed",
                "depth": child.context.depth,
                "mode": "background",
                "model": child.model,
                "round_limit": child.round_limit,
                "agent_task_id": agent_task_id,
            },
        )))

        agent_task_state = AgentTaskState(
            id=agent_task_id,
            agent_id=child.context.agent_id,
            agent_name=resolved_name,
            description=f"[Agent] {resolved_name}: {prompt[:80]}",
            prompt=prompt,
            parent_id=child.context.parent_id,
            is_persistent=is_persistent,
            tools=list(child.tools.keys()),
            skills=child.skills_override,
        )
        agent_task_state.inbox = asyncio.Queue()  # 子 Agent 的输入队列
        # 3.1 同步 child.context.inbox 与 handle.inbox，使外部消息能正确投递
        child.context.inbox = agent_task_state.inbox
        # 3.2 注入 agent_task_id，使 _loop() 的 PROGRESS 事件包含 task_id
        # SSEEmitter/WSEmitter 直接订阅 EventBus 时，可据此构造 task_progress 事件
        child.context.agent_task_id = agent_task_id
        self.session.agent_tasks.register(agent_task_state)
        logger.debug(
            "AgentTask registered | agent_task_id={} agent_id={}",
            agent_task_id, child.context.agent_id[:8]
        )

        # 4. 创建 Handle（outbox 已不再需要，移除该字段）
        handle = BackgroundAgentHandle(
            agent_id=child.context.agent_id,
            task_id=task_id,
            agent_task_id=agent_task_id,
            state=child.state,
            inbox=agent_task_state.inbox,
            agent_task_state=agent_task_state,
        )

        # 5. 通过父级 emitter 推送 task_started 事件（SSE/WebSocket）
        #    注意：self.emitter 在根 Agent 运行时是 SSEEmitter / WSEmitter
        if hasattr(self.emitter, "emit_task_started"):
            desc = agent_name or child.context.name or prompt[:80]
            self.emitter.emit_task_started(
                task_id=agent_task_id,
                task_type="local_agent",
                description=desc,
                pid=None,
            )

        # 6. 启动终端事件监听协程（订阅 EventBus → 更新 AgentTaskState → 注入父 Agent 通知）
        #    SSE/WS 事件推送已由 SSEEmitter/WSEmitter 直接订阅 EventBus 实现，
        #    此处不再需要向 parent_emitter 转发。
        #    此协程替代了旧的 forward_agent_events + _poll_agent_progress 两个竞争协程，
        #    同时替代 _notify_parent_done。
        child_agent_id = child.context.agent_id

        async def _watch_terminal_events():
            """
            订阅子 Agent 的 EventBus 终端事件，更新 AgentTaskState 并注入父 Agent 通知。

            职责（精简后）：
              - 收到 DONE      → mark_completed + _inject_done_notice
              - 收到 ERROR     → mark_failed    + _inject_done_notice
              - 收到 CANCELLED → mark_cancelled + _inject_done_notice

            事件推送（task_progress/task_done）已由 SSEEmitter/WSEmitter 直接订阅 EventBus 完成。
            订阅 filter：只处理来自本子 Agent 的终端事件。
            持续运行直到 handle._task 结束（teammate 场景下可能有多次任务完成）。
            """
            sub_id = f"terminal_{agent_task_id}"
            filter_fn = lambda e: (
                e.agent_id == child_agent_id
                and e.type in {EventType.DONE, EventType.ERROR, EventType.CANCELLED}
            )

            # 辅助函数：向父 Agent messages 注入完成通知（替代 _notify_parent_done）
            async def _inject_done_notice(
                result: str | None = None,
                cancelled: bool = False,
                error: str | None = None,
            ) -> None:
                if cancelled:
                    content = (
                        f"[Background agent '{agent_name}' (task_id={agent_task_id}) was cancelled.]"
                    )
                elif error:
                    content = (
                        f"[Background agent '{agent_name}' (task_id={agent_task_id}) failed: {error}]"
                    )
                else:
                    summary = (result or "")[:500]
                    if len(result or "") > 500:
                        summary += " ...(truncated)"
                    content = (
                        f"[Background agent '{agent_name}' (task_id={agent_task_id}) completed]\n"
                        f"Result: {summary}"
                    )
                done_message = {
                    "role": "system",
                    "content": content,
                    "_ccserver_background_agent_done": True,
                    "agent_task_id": agent_task_id,
                    "agent_name": agent_name,
                }
                self.context.messages.append(done_message)

                # 持久化到 session storage（若父 session 有 storage）
                if self.session.storage is not None:
                    self.session.storage.append_message(
                        self.session.id, done_message
                    )

                # hook: background_agent:done
                _hook_coro = self.session.hooks.emit_void(
                    "background_agent:done",
                    {
                        "agent_task_id": agent_task_id,
                        "agent_name": agent_name,
                        "result": result,
                        "cancelled": cancelled,
                        "error": error,
                    },
                    self._build_hook_ctx(),
                )
                if asyncio.iscoroutine(_hook_coro):
                    asyncio.create_task(_hook_coro)

                logger.debug(
                    "Parent notified (bus) | agent={} task_id={} cancelled={} error={}",
                    self.aid_label, agent_task_id, cancelled, error,
                )

            async with self.session.event_bus.subscribe(sub_id, filter_fn=filter_fn) as sub:
                while True:
                    # 等待事件，超时后检查任务是否已结束
                    event = await sub.get(timeout=30.0)
                    if event is None:
                        # 超时：检查任务是否已结束，是则退出
                        if handle._task is not None and handle._task.done():
                            break
                        continue

                    etype = event.type

                    if etype == EventType.DONE:
                        # 完成事件：更新 AgentTaskState，注入父 Agent 通知
                        content = event.payload.get("content", "")
                        if agent_task_state is not None:
                            agent_task_state.mark_completed(result=content)
                        await _inject_done_notice(result=content)
                        logger.info(
                            "AgentTask done (bus) | agent_task_id={} agent_id={}",
                            agent_task_id, child_agent_id[:8],
                        )
                        # 不 break，持续运行以覆盖 teammate 多次任务完成场景

                    elif etype == EventType.ERROR:
                        error_msg = event.payload.get("error", "unknown error")
                        if agent_task_state is not None:
                            agent_task_state.mark_failed(error=error_msg)
                        await _inject_done_notice(error=error_msg)
                        logger.warning(
                            "AgentTask failed (bus) | agent_task_id={} error={}",
                            agent_task_id, error_msg[:100],
                        )

                    elif etype == EventType.CANCELLED:
                        if agent_task_state is not None:
                            agent_task_state.mark_cancelled()
                        await _inject_done_notice(cancelled=True)

        asyncio.create_task(_watch_terminal_events())

        # 8. 启动后台 Agent 协程（不阻塞）
        async def _run_background():
            try:
                # P2: 使用 run_stream() 替代 run()，事件通过 BusEmitter 自动发布到 EventBus。
                # run_stream() 内部临时替换 emitter 为 BusEmitter，调用方通过 async for 消费事件。
                # 此处不需要手动消费事件（已由 EventBus 订阅者处理），直接遍历即可。
                async for _ in child.run_stream(prompt):
                    pass

                # ── Teammate 空闲循环：任务完成后进入 idle，等待新任务 ───────
                if is_teammate:
                    # 进入 idle 状态（持久 agent 在等待，不算结束）
                    await child._set_phase("idle")
                    registry = self.session.team_registry
                    if registry is not None:
                        from ccserver.team.models import TeamMemberState
                        registry.update_member_state_by_agent_id(
                            child.context.agent_id, TeamMemberState.IDLE
                        )
                        # 通过 EventBus 广播 idle 事件，Dispatcher 可订阅此事件实现事件驱动调度
                        await self.session.event_bus.publish(AgentEvent(
                            type=EventType.IDLE,
                            agent_id=child.context.agent_id,
                            session_id=self.session.id,
                            sender_type=SenderType.AGENT,
                            payload={"completed_task_id": task_id},
                        ))
                        logger.info(
                            "Teammate idle | agent_id={}", child.context.agent_id
                        )

                    # idle_timeout：等待新消息的超时时间（秒）
                    idle_timeout = 60.0
                    while True:
                        try:
                            msg = await asyncio.wait_for(
                                handle.inbox.get(), timeout=idle_timeout
                            )
                        except asyncio.TimeoutError:
                            # 超时：检查任务是否已被外部取消，否则继续等待
                            if handle._task is not None and handle._task.cancelled():
                                logger.info(
                                    "Teammate idle timeout+cancelled | agent_id={}",
                                    child.context.agent_id,
                                )
                                break
                            logger.debug(
                                "Teammate idle heartbeat | agent_id={}",
                                child.context.agent_id,
                            )
                            continue

                        match msg.get("type") or msg.get("msg_type"):
                            case MsgType.NEW_TASK:
                                task_prompt = msg.get("task_prompt") or msg.get("text", "")
                                if not task_prompt:
                                    continue
                                if registry is not None:
                                    registry.update_member_state_by_agent_id(
                                        child.context.agent_id, TeamMemberState.BUSY
                                    )
                                logger.info(
                                    "Teammate new task | agent_id={} task_id={}",
                                    child.context.agent_id, msg.get("task_id")
                                )
                                # P2: 使用 run_stream() 替代 run()，事件通过 BusEmitter 自动发布到 EventBus
                                async for _ in child.run_stream(task_prompt):
                                    pass
                                # 任务完成后重新进入 idle 状态（持久 agent 在等待）
                                await child._set_phase("idle")
                                if registry is not None:
                                    registry.update_member_state_by_agent_id(
                                        child.context.agent_id, TeamMemberState.IDLE
                                    )
                                    await self.session.event_bus.publish(AgentEvent(
                                        type=EventType.IDLE,
                                        agent_id=child.context.agent_id,
                                        session_id=self.session.id,
                                        sender_type=SenderType.AGENT,
                                        payload={"completed_task_id": msg.get("task_id")},
                                    ))

                            case MsgType.SHUTDOWN_REQUEST:
                                # 关闭请求：通过 BusEmitter 广播 done 事件后退出
                                logger.info(
                                    "Teammate shutdown | agent_id={}",
                                    child.context.agent_id
                                )
                                await self.session.event_bus.publish(AgentEvent(
                                    type=EventType.DONE,
                                    agent_id=child.context.agent_id,
                                    session_id=self.session.id,
                                    sender_type=SenderType.AGENT,
                                    payload={"content": "[shutdown by lead]"},
                                ))
                                break

                            case MsgType.CHAT:
                                # idle 状态下暂时忽略 chat，下次 child.run_stream() 时再处理
                                pass

                            case _:
                                logger.warning(
                                    "Teammate inbox unknown msg type ignored | agent_id={} msg={}",
                                    child.context.agent_id, msg,
                                )

            except asyncio.CancelledError:
                # 被取消：通过 EventBus 广播 cancelled 事件
                # _watch_terminal_events 订阅者会处理并注入父 Agent 通知
                await self.session.event_bus.publish(AgentEvent(
                    type=EventType.CANCELLED,
                    agent_id=child.context.agent_id,
                    session_id=self.session.id,
                    sender_type=SenderType.AGENT,
                ))
            except Exception as e:
                # 出错：通过 EventBus 广播 error 事件
                # _watch_terminal_events 订阅者会处理并注入父 Agent 通知
                await self.session.event_bus.publish(AgentEvent(
                    type=EventType.ERROR,
                    agent_id=child.context.agent_id,
                    session_id=self.session.id,
                    sender_type=SenderType.AGENT,
                    payload={"error": str(e)},
                ))
            finally:
                # 任务终结后从全局注册表注销
                unregister_handle(handle.agent_id)
                # 非永久驻留的 agent 从 agent_tasks 清理
                if not agent_task_state.is_persistent:
                    self.session.agent_tasks.evict(agent_task_id)

        handle._task = asyncio.create_task(_run_background())
        # 注册到全局句柄表，使 server.py 可以按 agent_id 查找并 cancel
        register_handle(handle)
        logger.info(
            "Background agent spawned | agent_task_id={} agent_id={} task_id={}",
            agent_task_id, handle.agent_id[:8], task_id
        )
        return handle

    async def _spawn_teammate(
        self,
        team_name: str,
        name: str,
        prompt: str,
        agent_def=None,
        model_override: str | None = None,
    ) -> BackgroundAgentHandle:
        """
        在指定 Team 中启动一个后台 teammate Agent。

        流程：
          1. 查找或创建 Team
          2. 注册 TeamMember（若不存在）
          3. 设置成员状态为 BUSY
          4. 使用确定性 agent_id 调用 spawn_background()
          5. 启动 Mailbox Poller
          6. 给 teammate 追加 Team 相关 system prompt
        """
        from ccserver.team.models import TeamMemberRole, TeamMemberState
        from ccserver.team.helpers import format_agent_id
        from ccserver.team.mailbox import TeamMailbox
        from ccserver.team.poller import TeamMailboxPoller
        from ccserver.team.prompts import build_teammate_system_addendum
        import dataclasses

        registry = self.session.team_registry
        if registry is None:
            raise RuntimeError("Team feature is not enabled.")

        team = registry.get_team(team_name)
        if team is None:
            team = registry.create_team(team_name)
            # 启动 Dispatcher
            from ccserver.team.dispatcher import TeamTaskDispatcher
            mailbox = TeamMailbox(team_name, self.session.storage)
            dispatcher = TeamTaskDispatcher(
                team, mailbox,
                task_manager=self.session.tasks,
                event_bus=self.session.event_bus,
            )
            dispatcher.start()
            # 反向挂载以便后续健康检查获取
            team._dispatcher = dispatcher
            team._mailbox = mailbox
        else:
            mailbox = getattr(team, "_mailbox", None)
            if mailbox is None:
                mailbox = TeamMailbox(team_name, self.session.storage)
                team._mailbox = mailbox

        agent_id = format_agent_id(name, team_name)

        if agent_id not in team.members:
            registry.add_member(team_name, name, role=TeamMemberRole.TEAMMATE)
        registry.update_member_state(team_name, agent_id, TeamMemberState.BUSY)

        # 构造带有 teammate addendum 的 agent_def 副本
        teammate_addendum = build_teammate_system_addendum(team_name, agent_id)
        effective_agent_def = agent_def
        if effective_agent_def is not None:
            new_system = effective_agent_def.system + teammate_addendum
            effective_agent_def = dataclasses.replace(effective_agent_def, system=new_system)
        else:
            # 没有 agent_def 时，创建一个最小化的匿名定义，只携带 addendum
            from ccserver.managers.agents.manager import AgentDef
            effective_agent_def = AgentDef(
                name=name,
                description=f"Teammate {name} in team {team_name}",
                system=teammate_addendum,
                location=self.session.project_root,
                is_teammate=True,
                is_team_capable=True,
            )

        handle = self.spawn_background(
            prompt=prompt,
            agent_def=effective_agent_def,
            agent_name=name,
            model_override=model_override,
            agent_id_override=agent_id,
            is_teammate=True,
            is_persistent=True,  # teammate 默认永久驻留
        )

        # 将 Team 名称挂载到 child Agent 上，供 _handle_send_message 读取
        # 由于 spawn_background 内部 child 是局部变量，我们通过 session.agent_tasks 反查
        agent_task = self.session.agent_tasks.get_by_agent_id(agent_id)
        if agent_task is not None:
            # agent_task.inbox 就是 handle.inbox，但这里不需要改 inbox
            pass

        # 设置当前 agent（如果是 Lead 自己）的 _team_name，供后续 SendMessage 使用
        self._team_name = team_name

        # 启动 EventBus SHUTDOWN 事件订阅者，将 shutdown 事件注入 handle.inbox
        # （作为 Mailbox/Poller 的实时通道补充，容灾时 Poller 仍可从 Mailbox 补投）
        async def _watch_shutdown_events():
            filter_fn = lambda e: e.type == EventType.SHUTDOWN and (
                e.to_agent == agent_id or e.to_agent is None
            )
            sub_id = f"shutdown_{agent_id}"
            async with self.session.event_bus.subscribe(sub_id, filter_fn=filter_fn) as sub:
                while True:
                    event = await sub.get(timeout=30.0)
                    if event is None:
                        if handle._task is None or handle._task.done():
                            break
                        continue
                    await handle.inbox.put({
                        "msg_type": MsgType.SHUTDOWN_REQUEST,
                        "from_agent": event.agent_id,
                        "reason": event.payload.get("reason"),
                    })
                    logger.info(
                        "Teammate shutdown received (EventBus) | agent_id={} from={}",
                        agent_id, event.agent_id,
                    )

        asyncio.create_task(_watch_shutdown_events())

        # 启动 Mailbox Poller，将持久化消息注入 handle.inbox（容灾备份）
        poller = TeamMailboxPoller(
            mailbox=mailbox,
            recipient=agent_id,
            inbox=handle.inbox,
            interval=3.0,
        )
        poller.start()
        handle._team_poller = poller  # type: ignore[attr-defined]

        logger.info(
            "Teammate spawned | team={} name={} agent_id={} agent_task_id={}",
            team_name, name, agent_id, handle.agent_task_id
        )
        return handle

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
        # _on_limit_ask_user 增加 round_limit 并设置 _continue_loop=True，
        # 本循环检测到后重置计数器继续执行，否则直接 return。
        self._continue_loop = False
        while True:
            self._continue_loop = False
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
                await self._maybe_compact()
                # 验证消息序列：修复被外部并发消息打断的 tool_use -> tool_result 对
                if Agent._sanitize_messages(self.context.messages):
                    if self.persist:
                        self.session.rewrite_messages(self.context.messages)
                logger.debug("Round {}/{} | agent={}", round_num + 1, self.round_limit, self.aid_label)
                # 调用前快照 messages（深拷贝，防止后续 append 污染记录）
                input_messages_snapshot = [dict(m) for m in self.context.messages]

                # 根据 stream 模式选择调用方式
                if self.stream:
                    response = await self._call_llm_stream()
                else:
                    response = await self._call_llm_sync()

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

                round_text = "".join(b["text"] for b in content if b.get("type") == "text")
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
                    await self._do_compact(reason="manual compact requested")

                self._append({"role": "user", "content": tool_results})

                await self._set_phase("running")

            # for 循环耗尽，达到轮次上限
            logger.warning("Round limit reached | agent={} limit={}", self.aid_label, self.round_limit)
            await self._set_phase("limit_reached")
            result = await self._on_limit(round_text)
            # _on_limit_ask_user 选择"继续"时设置 _continue_loop=True 并增加 round_limit
            if self._continue_loop:
                self.state.round_num = 0
                await self._set_phase("running")
                continue
            return result

    async def _on_limit(self, last_text: str) -> str:
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
            "agent:limit",
            {"last_text": last_text},
            self._build_hook_ctx(),
        )

        # Step 2：callback 优先
        if self.on_limit_callback is not None:
            try:
                result = await self.on_limit_callback(self, last_text)
                if result:
                    return await self._finish_with_last_text(result)
            except Exception as e:
                logger.error("on_limit_callback failed | agent={} error={}", self.aid_label, e)
            # 回调失败或返回空，fallback 到 last_text 策略

        strategy = self.limit_strategy

        if strategy == "ask_user":
            return await self._on_limit_ask_user(last_text)
        elif strategy == "graceful":
            return await self._on_limit_graceful(last_text)
        elif strategy == "summarize":
            return await self._on_limit_summarize(last_text)
        elif strategy == "report" and not self.context.is_orchestrator:
            rounds = self.round_limit
            report = f"[LIMIT_REACHED] 已执行 {rounds} 轮，部分结果：{last_text or '（无输出）'}"
            return report
        else:
            # last_text（默认）或 callback 无回调 fallback，或子 agent 用了主 agent 专属策略
            return await self._finish_with_last_text(last_text)

    async def _finish_with_last_text(self, last_text: str) -> str:
        """兜底输出 last_text，走正常结束流程。无 last_text 时 emit_error。"""
        if last_text:
            if self.context.is_orchestrator:
                await self.session.hooks.emit_void(
                    "agent:stop",
                    {"reply": last_text},
                    self._build_hook_ctx(),
                )
                await self.emitter.emit_done(last_text)
            else:
                await self.emitter.emit_subagent_done(last_text)
            return last_text
        else:
            await self.emitter.emit_error("Round limit reached with no output")
            return ""

    async def _on_limit_ask_user(self, last_text: str) -> str:
        """向用户询问是否继续（仅主 agent 有意义）。"""
        if not self.context.is_orchestrator:
            return await self._finish_with_last_text(last_text)
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
            # 追加 user 消息触发下一轮；增加轮次上限，让外层 while 重入
            self.context.messages.append({"role": "user", "content": "继续执行未完成的任务。"})
            self.round_limit += MAIN_ROUND_LIMIT
            self._continue_loop = True
            logger.info("User chose to continue | agent={} new_limit={}", self.aid_label, self.round_limit)
            return ""
        return await self._finish_with_last_text(last_text)

    async def _on_limit_graceful(self, last_text: str) -> str:
        """向用户输出固定提示后优雅结束。"""
        graceful_msg = "处理步骤超出限制，请重新提问或简化需求。"
        if last_text:
            graceful_msg = f"{graceful_msg}\n\n目前结果：{last_text}"
        if self.context.is_orchestrator:
            await self.session.hooks.emit_void(
                "agent:stop",
                {"reply": graceful_msg},
                self._build_hook_ctx(),
            )
            await self.emitter.emit_done(graceful_msg)
        else:
            await self.emitter.emit_subagent_done(graceful_msg)
        return graceful_msg

    async def _on_limit_summarize(self, last_text: str) -> str:
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
            assert response.content, f"LLM returned empty content in _on_limit_summarize for {self.aid_label}"
            # 跳过 ThinkingBlock，取第一个 TextBlock（deepseek 等端点默认开启 thinking）
            text_block = next((b for b in response.content if getattr(b, "type", None) == "text"), None)
            assert text_block is not None, f"_on_limit_summarize: no TextBlock in response, types={[getattr(b,'type',None) for b in response.content]}"
            summary = text_block.text
        except Exception as e:
            logger.error("_on_limit_summarize failed | agent={} error={}", self.aid_label, e)
            return await self._finish_with_last_text(last_text)

        result = f"（步骤超限，以下为当前进度摘要）\n\n{summary}"
        if self.context.is_orchestrator:
            await self.session.hooks.emit_void(
                "agent:stop",
                {"reply": result},
                self._build_hook_ctx(),
            )
            await self.emitter.emit_done(result)
        else:
            await self.emitter.emit_subagent_done(result)
        return result

    # ── 消息序列验证 ──────────────────────────────────────────────────────────

    @staticmethod
    def _sanitize_messages(messages: list[dict]) -> bool:
        """
        验证并修复消息序列，确保符合 Anthropic API 的消息顺序要求。

        API 规则：assistant 消息中包含 tool_use 块时，下一条消息必须是 user 角色，
        且包含对应的 tool_result 块（tool_use_id 匹配）。

        如果外部消息（如用户通过 channel 发送的新输入）被并发插入到 tool_use 和
        tool_result 之间，会导致 API 报 "tool call result does not follow tool call" 错误。

        修复方式：在不完整的 tool_use 后插入空的 tool_result，将外部消息后移。

        Args:
            messages: 消息列表（会被原地修改）

        Returns:
            是否做了修复
        """
        from ccserver.utils import get_block_attr

        fixed = False
        i = 0
        while i < len(messages):
            msg = messages[i]
            if msg.get("role") != "assistant":
                i += 1
                continue

            # 检查 assistant 消息是否包含 tool_use 块
            content = msg.get("content", [])
            if not isinstance(content, list):
                i += 1
                continue

            tool_use_ids = [
                get_block_attr(b, "id")
                for b in content
                if isinstance(b, dict) and get_block_attr(b, "type") == "tool_use"
                if get_block_attr(b, "id")
            ]
            if not tool_use_ids:
                i += 1
                continue

            # 检查下一条消息
            if i + 1 >= len(messages):
                # tool_use 是列表最后一条，上一轮可能中断
                tool_results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": "[Tool call was interrupted. No result available.]",
                    }
                    for tid in tool_use_ids
                ]
                messages.append({
                    "role": "user",
                    "content": tool_results,
                })
                logger.warning(
                    "Fixed dangling tool_use at end | tool_use_ids={}",
                    tool_use_ids,
                )
                fixed = True
                break

            next_msg = messages[i + 1]
            if next_msg.get("role") != "user":
                # 下一条不是 user，序列被破坏（可能是外部 system/user 消息插入）
                tool_results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": "[Tool call was interrupted by new input. No result available.]",
                    }
                    for tid in tool_use_ids
                ]
                messages.insert(i + 1, {
                    "role": "user",
                    "content": tool_results,
                })
                logger.warning(
                    "Fixed broken tool_use sequence | tool_use_ids={} next_role={}",
                    tool_use_ids, next_msg.get("role"),
                )
                fixed = True
                i += 2  # 跳过插入的 tool_result，继续检查后续
                continue

            # 下一条是 user，检查 content 是否包含对应的 tool_result
            next_content = next_msg.get("content", [])
            if isinstance(next_content, str):
                # user 消息的 content 是字符串（普通文本），不是 tool_result
                tool_results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": "[Tool call was interrupted by new input. No result available.]",
                    }
                    for tid in tool_use_ids
                ]
                messages.insert(i + 1, {
                    "role": "user",
                    "content": tool_results,
                })
                logger.warning(
                    "Fixed broken tool_use sequence | tool_use_ids={} next_content=string",
                    tool_use_ids,
                )
                fixed = True
                i += 2
                continue

            # next_content 是 list，检查是否包含所有对应的 tool_result
            result_ids = set()
            for block in next_content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tid = block.get("tool_use_id")
                    if tid:
                        result_ids.add(tid)

            missing_ids = set(tool_use_ids) - result_ids
            if missing_ids:
                # 部分 tool_use 没有对应的 tool_result（比较少见）
                for tid in missing_ids:
                    next_content.append({
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": "[Tool call was interrupted by new input. No result available.]",
                    })
                logger.warning(
                    "Fixed partial tool_use sequence | missing_ids={}",
                    list(missing_ids),
                )
                fixed = True

            i += 1

        return fixed

    # ── 工具处理 ──────────────────────────────────────────────────────────────

    async def _call_llm_stream(self):
        """
        流式调用 LLM，实时 emit token。用于 stream=True。
        失败时返回 None。
        """
        import asyncio
        import httpx
        from anthropic import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

        max_retries = 3
        retry_delays = [2, 5, 10]

        await self._set_phase("llm_calling")
        for attempt in range(max_retries):
            try:
                # hook: prompt:build:before — 可修改 system/messages
                build_hook = await self.session.hooks.emit(
                    "prompt:build:before",
                    {
                        "system": self.system,
                        "messages": [dict(m) for m in self.context.messages],
                        "model": self.model,
                    },
                    self._build_hook_ctx(),
                )
                # hook 可修改 system（替换或追加）
                effective_system = build_hook.system_message or self.system
                # hook 可追加 additional_context 到最后一条 user 消息
                if build_hook.additional_context:
                    msgs = [dict(m) for m in self.context.messages]
                    if msgs and msgs[-1].get("role") == "user":
                        msgs[-1]["content"] = msgs[-1].get("content", "") + "\n\n" + build_hook.additional_context
                    effective_messages = msgs
                else:
                    effective_messages = [dict(m) for m in self.context.messages]

                # hook: prompt:llm:input — 观测即将发送给 LLM 的完整输入
                await self.session.hooks.emit_void(
                    "prompt:llm:input",
                    {"messages": effective_messages, "model": self.model},
                    self._build_hook_ctx(),
                )
                # 兜底验证：hook 可能修改了消息序列，确保 tool_use -> tool_result 配对完整
                Agent._sanitize_messages(effective_messages)

                # 发布 llm_request 事件，供 monitor 追踪 LLM 调用
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
                        "attempt": attempt + 1,
                    },
                ))

                async with self.adapter.stream(
                    model=self.model,
                    system=effective_system,
                    messages=effective_messages,
                    tools=self._schemas,
                    max_tokens=8000,
                ) as stream:
                    # 遍历完整事件流，区分 text_delta（正文）和 thinking_delta（思考过程）
                    async for chunk in stream:
                        chunk_type = getattr(chunk, "type", None)
                        if chunk_type == "content_block_delta":
                            delta = getattr(chunk, "delta", None)
                            delta_type = getattr(delta, "type", None)
                            if delta_type == "text_delta":
                                await self.emitter.emit_token(getattr(delta, "text", ""))
                            elif delta_type == "thinking_delta":
                                await self.emitter.emit_thinking(getattr(delta, "thinking", ""))
                    response = await stream.get_final_message()

                # 发布 llm_response 事件
                llm_duration_ms = int((datetime.now(timezone.utc) - llm_start_ts).total_seconds() * 1000)
                content_blocks = response.content if hasattr(response, "content") else []
                await self.session.event_bus.publish(AgentEvent(
                    type=EventType.LLM_RESPONSE,
                    agent_id=self.context.agent_id,
                    session_id=self.session.id,
                    sender_type=SenderType.AGENT,
                    payload={
                        "model": self.model,
                        "stop_reason": response.stop_reason,
                        "content_blocks_count": len(content_blocks),
                        "duration_ms": llm_duration_ms,
                    },
                ))
                return response

            except (APIConnectionError, APITimeoutError, httpx.RemoteProtocolError, InternalServerError, RateLimitError) as e:
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
                    await self.session.hooks.emit_void(
                        "prompt:llm:error",
                        {"error": str(e), "model": self.model},
                        self._build_hook_ctx(),
                    )
                    return None

            except Exception as e:
                logger.error(
                    "LLM error | agent={} exc_type={} error={}",
                    self.aid_label, type(e).__name__, e,
                )
                await self.emitter.emit_error(str(e))
                await self.session.hooks.emit_void(
                    "prompt:llm:error",
                    {"error": str(e), "model": self.model},
                    self._build_hook_ctx(),
                )
                return None

        return None  # 不会到达

    async def _call_llm_sync(self):
        """
        非流式调用 LLM，不 emit token。用于 stream=False。
        失败时返回 None。
        """
        import asyncio
        import httpx
        from anthropic import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

        max_retries = 3
        retry_delays = [2, 5, 10]

        await self._set_phase("llm_calling")
        for attempt in range(max_retries):
            try:
                # hook: prompt:build:before — 可修改 system/messages
                build_hook = await self.session.hooks.emit(
                    "prompt:build:before",
                    {
                        "system": self.system,
                        "messages": [dict(m) for m in self.context.messages],
                        "model": self.model,
                    },
                    self._build_hook_ctx(),
                )
                # hook 可修改 system（替换或追加）
                effective_system = build_hook.system_message or self.system
                # hook 可追加 additional_context 到最后一条 user 消息
                if build_hook.additional_context:
                    msgs = [dict(m) for m in self.context.messages]
                    if msgs and msgs[-1].get("role") == "user":
                        msgs[-1]["content"] = msgs[-1].get("content", "") + "\n\n" + build_hook.additional_context
                    effective_messages = msgs
                else:
                    effective_messages = [dict(m) for m in self.context.messages]

                # hook: prompt:llm:input
                await self.session.hooks.emit_void(
                    "prompt:llm:input",
                    {"messages": effective_messages, "model": self.model},
                    self._build_hook_ctx(),
                )
                # 兜底验证：hook 可能修改了消息序列，确保 tool_use -> tool_result 配对完整
                Agent._sanitize_messages(effective_messages)

                # 发布 llm_request 事件，供 monitor 追踪 LLM 调用
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
                        "attempt": attempt + 1,
                    },
                ))

                response = await self.adapter.create(
                    model=self.model,
                    system=effective_system,
                    messages=effective_messages,
                    tools=self._schemas,
                    max_tokens=8000,
                )

                # 发布 llm_response 事件
                llm_duration_ms = int((datetime.now(timezone.utc) - llm_start_ts).total_seconds() * 1000)
                content_blocks = response.content if hasattr(response, "content") else []
                await self.session.event_bus.publish(AgentEvent(
                    type=EventType.LLM_RESPONSE,
                    agent_id=self.context.agent_id,
                    session_id=self.session.id,
                    sender_type=SenderType.AGENT,
                    payload={
                        "model": self.model,
                        "stop_reason": response.stop_reason,
                        "content_blocks_count": len(content_blocks),
                        "duration_ms": llm_duration_ms,
                    },
                ))
                return response

            except (APIConnectionError, APITimeoutError, httpx.RemoteProtocolError, InternalServerError, RateLimitError) as e:
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
                    await self.session.hooks.emit_void(
                        "prompt:llm:error",
                        {"error": str(e), "model": self.model},
                        self._build_hook_ctx(),
                    )
                    return None

            except Exception as e:
                logger.error(
                    "LLM error | agent={} exc_type={} error={}",
                    self.aid_label, type(e).__name__, e,
                )
                await self.emitter.emit_error(str(e))
                await self.session.hooks.emit_void(
                    "prompt:llm:error",
                    {"error": str(e), "model": self.model},
                    self._build_hook_ctx(),
                )
                return None

        return None  # 不会到达

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

    async def _maybe_compact(self):
        self.compactor.micro(self.context.messages)
        msg_count = len(self.context.messages)
        # 两种触发压缩的条件：
        # 1. token 数超过阈值（由 compactor.needs_compact 判断）
        # 2. 消息条数超过 300 条（防止短消息过多导致内存无限增长）
        if msg_count > 300:
            logger.info(
                "Agent message count exceeds limit | agent={} msgs={} "
                "triggering compact",
                self.aid_label, msg_count,
            )
            await self._do_compact(reason="message count limit reached")
            return
        if self.compactor.needs_compact(self.context.messages):
            logger.debug(f"do compact")
            await self._do_compact(reason="token threshold reached")

    async def _do_compact(self, reason: str):
        message_count = len(self.context.messages)
        # hook: agent:compact:before（observing）
        await self.session.hooks.emit_void(
            "agent:compact:before",
            {"message_count": message_count, "token_count": 0},  # token_count 暂不计算
            self._build_hook_ctx(),
        )
        await self.emitter.emit_compact(reason)
        lib = self.prompt_engine
        compacted = await self.compactor.compact(
            self.session,
            self.emitter,
            self.context.messages,
            lib=lib,
        )
        compacted_count = message_count - len(compacted)
        if self.persist:
            self.session.rewrite_messages(compacted)
        else:
            self.context.messages[:] = compacted
        # hook: agent:compact:after（observing）
        await self.session.hooks.emit_void(
            "agent:compact:after",
            {"compacted_count": compacted_count, "summary_length": 0, "tokens_before": 0, "tokens_after": 0},
            self._build_hook_ctx(),
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

