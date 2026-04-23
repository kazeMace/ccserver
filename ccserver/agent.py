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
from ccserver.emitters.queue import QueueEmitter
from .agent_handle import BackgroundAgentHandle, forward_agent_events, _poll_agent_progress
from .agent_registry import register_handle, unregister_handle
from .model import ModelAdapter, get_adapter
from .emitters.queue import QueueEmitter

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

    async def run(self, message: str, outbox: "QueueEmitter | None" = None) -> str:
        """
        追加用户消息并执行循环。

        Args:
            message: 用户输入的原始消息。
            outbox:  可选的后台任务输出队列（QueueEmitter）。
                    当通过 spawn_background() 调用时传入，用于向外部推送
                    status_request 响应等事件。
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
        return await self._loop(outbox=outbox)

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
        # 子代理继承父代理的环境变量
        child_env_vars = dict(self.context.env_vars)
        child_context = AgentContext(
            name=agent_name,
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
        # 优先级：model_override > agent_def.model > 继承父 agent 的 model
        child_model = model_override or (agent_def.model if agent_def and agent_def.model else None) or self.model

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

        # 让 prompt lib 对 schema 描述做后处理（如 cc_reverse 替换为 CC 原版描述）
        from ccserver.prompts_lib.adapter import get_lib
        child._schemas = get_lib(child.prompt_version).patch_tool_schemas(child._schemas)

        child.recorder.schemas = child._schemas

        return child

# ── 父 Agent 通知 ───────────────────────────────────────────────────────


    async def _notify_parent_done(
        parent_agent: "Agent",
        agent_task_id: str,
        agent_name: str,
        result: str | None,
        cancelled: bool = False,
        error: str | None = None,
    ) -> None:
        """
        后台 Agent 完成后，向父 Agent 的 messages 注入系统通知。

        注入的消息为 system role 的 "background_agent_done" 类型，
        父 Agent 的 _loop() 在 append_message 时感知到后，
        自然进入下一轮 LLM 调用，将该信息纳入上下文。

        Args:
            parent_agent:  父 Agent 实例（spawn_background 的 self）。
            agent_task_id: 后台任务的唯一 ID（"a" + uuid[:8]）。
            agent_name:    后台 Agent 的名称。
            result:        Agent 的最终输出（正常完成时）。
            cancelled:     是否被取消。
            error:         错误信息（异常结束时）。
        """
        if cancelled:
            content = (
                f"[Background agent '{agent_name}' (task_id={agent_task_id}) was cancelled.]"
            )
        elif error:
            content = (
                f"[Background agent '{agent_name}' (task_id={agent_task_id}) failed: {error}]"
            )
        else:
            # 截断过长结果，避免污染上下文
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
            # 标注类型，供父 Agent 识别（不会被 compact 压缩掉）
            "_ccserver_background_agent_done": True,
            "agent_task_id": agent_task_id,
            "agent_name": agent_name,
        }

        # 注入到父 Agent 的消息列表（即使父 Agent 正在运行，下一轮也会感知到）
        parent_agent.context.messages.append(done_message)

        # 持久化到 session storage（若父 session 有 storage）
        if parent_agent.session.storage is not None:
            parent_agent.session.storage.append_message(
                parent_agent.session.id, done_message
            )

        # hook: background_agent:done — 可拦截通知、修改 content、甚至注入额外消息
        # 以 fire-and-forget 方式执行，不阻塞后台协程
        import asyncio as _asyncio
        _hook_coro = parent_agent.session.hooks.emit_void(
            "background_agent:done",
            {
                "agent_task_id": agent_task_id,
                "agent_name": agent_name,
                "result": result,
                "cancelled": cancelled,
                "error": error,
            },
            parent_agent._build_hook_ctx(),
        )
        if _asyncio.iscoroutine(_hook_coro):
            _asyncio.create_task(_hook_coro)

        logger.debug(
            "Parent notified | agent={} task_id={} cancelled={} error={}",
            parent_agent.aid_label, agent_task_id, cancelled, error
        )

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

        # 2. 替换 Emitter 为 QueueEmitter（内部事件收集）
        queue_emitter = QueueEmitter()
        child.emitter = queue_emitter

        # 3. 创建 AgentTaskState 并注册到 Session
        agent_task_state = AgentTaskState(
            id=agent_task_id,
            agent_id=child.context.agent_id,
            agent_name=agent_name or child.context.name or None,
            description=f"[Agent] {agent_name or child.context.name or 'background'}: {prompt[:80]}",
            prompt=prompt,
        )
        agent_task_state.inbox = asyncio.Queue()  # 绑定 Handle 的 inbox
        agent_task_state.outbox = queue_emitter.queue  # 绑定 QueueEmitter 的队列
        # 3.1 同步 child.context.inbox 与 handle.inbox，使外部消息能正确投递
        #     （_drain_inbox_and_respond 读取 context.inbox，poll 协程写入 handle.inbox）
        child.context.inbox = agent_task_state.inbox
        self.session.agent_tasks.register(agent_task_state)
        logger.debug(
            "AgentTask registered | agent_task_id={} agent_id={}",
            agent_task_id, child.context.agent_id[:8]
        )

        # 4. 创建 Handle（inbox 已由 agent_task_state 持有，outbox 同上）
        handle = BackgroundAgentHandle(
            agent_id=child.context.agent_id,
            task_id=task_id,
            agent_task_id=agent_task_id,
            state=child.state,
            inbox=agent_task_state.inbox,
            outbox=agent_task_state.outbox,
            agent_task_state=agent_task_state,
        )

        # 5. 通过父级 emitter 推送 task_started 事件（SSE/WebSocket）
        #    注意：self.emitter 在根 Agent 运行时是 SSEEmitter / WSEmitter
        if hasattr(self.emitter, "emit_task_started"):
            # 构建描述：优先用 agent_name，其次用 context.name，最后用 prompt 前 80 字符
            desc = agent_name or child.context.name or prompt[:80]
            self.emitter.emit_task_started(
                task_id=agent_task_id,
                task_type="local_agent",
                description=desc,
                pid=None,  # Agent 无 OS 进程
            )

        # 6. 启动事件转发协程（监听 outbox → 推送 task_done/progress）
        asyncio.create_task(
            forward_agent_events(handle, self.emitter)
        )

        # 7. 启动 progress 轮询协程（Path B：定期向 inbox 注入 status_request）
        #    _poll_agent_progress 从 outbox 读取 progress 响应并透传给父级 emitter
        asyncio.create_task(
            _poll_agent_progress(handle, self.emitter, interval=5.0)
        )

        # 8. 启动后台 Agent 协程（不阻塞）
        async def _run_background():
            try:
                # 传入 queue_emitter 作为 outbox，使 _loop() 能向 outbox 写 progress 事件
                result = await child.run(prompt, outbox=queue_emitter)
                # 写入 outbox，由 forward_agent_events 消费并推送 task_done
                await handle.outbox.put({"type": "done", "content": result})
                # ── 通知父 Agent：向其 messages 注入完成通知 ─────────────────
                # 这样父 Agent 的 _loop() 在 append_message 时能感知到结果，
                # 自然触发下一轮 LLM 调用处理该结果（若需要）。
                await self._notify_parent_done(
                    agent_task_id=agent_task_id,
                    agent_name=agent_name or child.context.name or "background",
                    result=result,
                )

                # ── Teammate 空闲循环：任务完成后进入 idle，等待新任务 ───────
                if is_teammate:
                    registry = self.session.team_registry
                    if registry is not None:
                        from ccserver.team.models import TeamMemberState
                        registry.update_member_state_by_agent_id(
                            child.context.agent_id, TeamMemberState.IDLE
                        )
                        logger.info(
                            "Teammate idle | agent_id={}", child.context.agent_id
                        )

                    # idle_timeout：等待新消息的超时时间（秒）。
                    # 超时后检查 handle 是否已被取消，避免 _poll_agent_progress
                    # 停止后协程永久 hang 在 inbox.get()。
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
                            case MsgType.STATUS_REQUEST:
                                # 进度查询：轮询协程注入，idle 状态下直接跳过
                                continue

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
                                result = await child.run(task_prompt, outbox=queue_emitter)
                                await handle.outbox.put({"type": "done", "content": result})
                                await self._notify_parent_done(
                                    agent_task_id=agent_task_id,
                                    agent_name=agent_name or child.context.name or "background",
                                    result=result,
                                )
                                if registry is not None:
                                    registry.update_member_state_by_agent_id(
                                        child.context.agent_id, TeamMemberState.IDLE
                                    )

                            case MsgType.SHUTDOWN_REQUEST:
                                # 关闭请求：优雅退出，通知 outbox 后跳出循环
                                logger.info(
                                    "Teammate shutdown | agent_id={}",
                                    child.context.agent_id
                                )
                                await handle.outbox.put(
                                    {"type": "done", "content": "[shutdown by lead]"}
                                )
                                break

                            case MsgType.CHAT:
                                # chat 消息在下次 child.run() 时由 _drain_inbox_and_respond 消费
                                # idle 状态下暂时忽略，因为没有正在运行的 _loop()
                                pass

                            case _:
                                logger.warning(
                                    "Teammate inbox unknown msg type ignored | agent_id={} msg={}",
                                    child.context.agent_id, msg,
                                )

            except asyncio.CancelledError:
                await handle.outbox.put({"type": "cancelled"})
                await self._notify_parent_done(
                    agent_task_id=agent_task_id,
                    agent_name=agent_name or child.context.name or "background",
                    result=None,
                    cancelled=True,
                )
            except Exception as e:
                await handle.outbox.put({"type": "error", "error": str(e)})
                await self._notify_parent_done(
                    agent_task_id=agent_task_id,
                    agent_name=agent_name or child.context.name or "background",
                    result=None,
                    error=str(e),
                )
            finally:
                # 任务终结后从全局注册表注销
                unregister_handle(handle.agent_id)

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
            # 启动 Dispatcher 和 PermissionRelay（骨架，后续扩展）
            from ccserver.team.dispatcher import TeamTaskDispatcher
            from ccserver.team.permission_relay import TeamPermissionRelay
            mailbox = TeamMailbox(team_name, self.session.storage)
            dispatcher = TeamTaskDispatcher(team, mailbox, task_manager=self.session.tasks)
            dispatcher.start()
            relay = TeamPermissionRelay(team, mailbox)
            relay.start()
            # 反向挂载以便后续健康检查获取
            team._dispatcher = dispatcher
            team._relay = relay
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
        )

        # 将 Team 名称挂载到 child Agent 上，供 _handle_send_message 读取
        # 由于 spawn_background 内部 child 是局部变量，我们通过 session.agent_tasks 反查
        agent_task = self.session.agent_tasks.get_by_agent_id(agent_id)
        if agent_task is not None:
            # agent_task.inbox 就是 handle.inbox，但这里不需要改 inbox
            pass

        # 设置当前 agent（如果是 Lead 自己）的 _team_name，供后续 SendMessage 使用
        self._team_name = team_name

        # 启动 Mailbox Poller，将持久化消息注入 handle.inbox
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

    async def _drain_inbox_and_respond(self, outbox: "QueueEmitter | None") -> list[dict]:
        """
        非阻塞读取 inbox，处理 status_request 并写入 progress 响应到 outbox。
        同时处理 Agent Team 相关的 mailbox 消息（new_task, shutdown_request, chat 等）。

        外部轮询协程（_poll_agent_progress）定期向 child.inbox 注入 status_request，
        此方法在每轮回合开始时被调用，处理积压的请求。

        Returns:
            需要追加到 messages 的新消息列表（如 new_task, shutdown_request, chat）
        """
        new_messages: list[dict] = []

        # 合并为单个循环，同时处理 status_request（进度回报）和 Team Mailbox 消息
        while True:
            try:
                msg = self.context.inbox.get_nowait()
            except asyncio.QueueEmpty:
                break

            # status_request 用 type 字段标识（非 Mailbox 消息，由轮询协程注入）
            # 其余消息用 msg_type 字段标识（来自 TeamMailboxPoller）
            match msg.get("type") or msg.get("msg_type"):
                case MsgType.STATUS_REQUEST:
                    # 进度查询：由 _poll_agent_progress 定期注入，回报当前轮次/阶段给父 Agent
                    if outbox is not None:
                        progress = {
                            "round_num": self.state.round_num,
                            "max_rounds": self.round_limit,
                            "phase": self.state.phase,
                            "current_tool": self.state.current_tool,
                        }
                        await outbox.put({"type": "progress", **progress})

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
                        "_ccserver_team_shutdown": True,
                    })

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

                case _:
                    # 未知消息类型，记录警告但不中断循环
                    logger.warning(
                        "Inbox unknown msg type ignored | agent={} msg={}",
                        self.aid_label,
                        msg,
                    )

        return new_messages

    async def _loop(self, outbox: "QueueEmitter | None" = None) -> str:
        self.state.start_time = datetime.now(timezone.utc)
        self.state.phase = "running"
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
                # Path B: 处理积压的 status_request，写 progress 到 outbox
                # 同时处理 Team Mailbox 消息（new_task, shutdown_request, chat 等）
                team_messages = await self._drain_inbox_and_respond(outbox)
                for tm in team_messages:
                    self._append(tm)
                if any(m.get("_ccserver_team_shutdown") for m in team_messages):
                    self.state.phase = "done"
                    return round_text + "\n[shutdown by lead]"
                await self._maybe_compact()
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
                    self.state.phase = "error"
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
                    self.state.phase = "done"
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

                self.state.phase = "tool_executing"
                tool_results, trigger_compact = await self._handle_tools(response.content)
                self._append({"role": "user", "content": tool_results})

                if trigger_compact:
                    await self._do_compact(reason="manual compact requested")

                self.state.phase = "running"

            # for 循环耗尽，达到轮次上限
            logger.warning("Round limit reached | agent={} limit={}", self.aid_label, self.round_limit)
            self.state.phase = "limit_reached"
            result = await self._on_limit(round_text)
            # _on_limit_ask_user 选择"继续"时设置 _continue_loop=True 并增加 round_limit
            if self._continue_loop:
                self.state.round_num = 0
                self.state.phase = "running"
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
            summary = response.content[0].text
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

    # ── 工具处理 ──────────────────────────────────────────────────────────────

    async def _call_llm_stream(self):
        """
        流式调用 LLM，实时 emit token。用于 stream=True。
        失败时返回 None。
        """
        import asyncio
        import httpx
        from anthropic import APIConnectionError, APITimeoutError

        max_retries = 3
        retry_delays = [2, 5, 10]

        self.state.phase = "llm_calling"
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
                async with self.adapter.stream(
                    model=self.model,
                    system=effective_system,
                    messages=effective_messages,
                    tools=self._schemas,
                    max_tokens=8000,
                ) as stream:
                    async for text in stream.text_stream:
                        await self.emitter.emit_token(text)
                    response = await stream.get_final_message()
                return response

            except (APIConnectionError, APITimeoutError, httpx.RemoteProtocolError) as e:
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
                logger.error("LLM error | agent={} error={}", self.aid_label, e)
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
        from anthropic import APIConnectionError, APITimeoutError

        max_retries = 3
        retry_delays = [2, 5, 10]

        self.state.phase = "llm_calling"
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
                response = await self.adapter.create(
                    model=self.model,
                    system=effective_system,
                    messages=effective_messages,
                    tools=self._schemas,
                    max_tokens=8000,
                )
                return response

            except (APIConnectionError, APITimeoutError, httpx.RemoteProtocolError) as e:
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
                logger.error("LLM error | agent={} error={}", self.aid_label, e)
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
            await self.emitter.emit_tool_start(name, preview)

            if name == "Agent":
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
                    logger.debug("Tool result| agent={} tool={} result={!r}", self.aid_label, name, result.content[:200] if result.content else "")
                else:
                    logger.warning("Unknown tool | agent={} tool={}", self.aid_label, name)
                    result = ToolResult.error(f"Unknown tool: {name}")

            await self.emitter.emit_tool_result(name, result.content)
            # hook: tool:call:after / tool:call:failure（observing）
            if result.is_error:
                await self.session.hooks.emit_void(
                    "tool:call:failure",
                    {"tool_name": name, "tool_use_id": block_id, "tool_input": input_, "error": result.content or ""},
                    self._build_hook_ctx(),
                )
            else:
                await self.session.hooks.emit_void(
                    "tool:call:after",
                    {"tool_name": name, "tool_use_id": block_id, "tool_input": input_, "tool_response": result.content or ""},
                    self._build_hook_ctx(),
                )
            results.append(result.to_api_dict(block_id))

        return results, trigger_compact

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
        subagent_type = task_input.get("subagent_type", "")
        agent_name = task_input.get("description", "") or subagent_type
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
        summary = await child._loop()
        logger.info(
            "Child agent done   | child={} summary_len={}",
            child.context.agent_id[:8], len(summary)
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
            mailbox.broadcast(chat_msg, recipients=recipients, exclude=from_agent)
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
            mailbox.send(chat_msg)
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
        message_count = len(self.context.messages)
        # hook: agent:compact:before（observing）
        await self.session.hooks.emit_void(
            "agent:compact:before",
            {"message_count": message_count, "token_count": 0},  # token_count 暂不计算
            self._build_hook_ctx(),
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

