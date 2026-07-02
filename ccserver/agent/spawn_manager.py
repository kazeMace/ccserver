"""
agent.spawn_manager — 子 Agent / 后台 Agent / teammate 的派生与生命周期管理。

背景：
  原 Agent 把以下派生逻辑写在自身:
    spawn_child         同步派生一个子 Agent(独立上下文)
    spawn_background    启动后台 Agent(非阻塞,返回 handle),含终端事件监听与
                        teammate 空闲循环两个闭包
    _spawn_teammate     在 Team 中启动持久 teammate(挂载 mailbox/poller)
    _resolve_model_hint model_hint 快捷方式解析

设计：
  抽出 SpawnManager,作为这些派生逻辑的归属地。它只依赖 AgentRuntime 契约,
  通过 rt.* 访问父 Agent 的 session/context/emitter 等。
  父 Agent 保留 spawn_child / spawn_background 公共方法作为薄委托 wrapper,
  以保持对外 API 不变(agent_scheduler、测试等仍可直接调用)。

  行为与重构前逐字一致(含所有 hook、EventBus 事件、闭包逻辑)。
"""

from __future__ import annotations

import asyncio

from loguru import logger

from ..event_bus import AgentEvent, EventType, SenderType
from ..emitters.bus_emitter import BusEmitter
from ..agent_handle import BackgroundAgentHandle
from ..agent_registry import register_handle, unregister_handle
from ..team.protocol import MsgType
from ..tasks.agent import AgentTaskStatus
from .runtime import AgentRuntime


class SpawnManager:
    """
    Agent 派生管理器,被 Agent 持有(组合)。

    依赖 AgentRuntime 提供父 Agent 的 session/context/emitter/adapter/model 等。
    """

    def __init__(self, rt: AgentRuntime):
        self._rt = rt

    # ── model_hint 解析 ──────────────────────────────────────────────────────────

    @staticmethod
    def resolve_model_hint(hint: str) -> str | None:
        """
        将 model_hint 快捷方式解析为具体模型名。

        支持的 hint：
          "haiku"   → claude-haiku-4-5-20251001（Anthropic Haiku）
          "sonnet"  → claude-sonnet-4-6（当前默认 Sonnet）
          "opus"    → claude-opus-4-7（Anthropic Opus）
          "inherit" → None（由调用方使用父模型）

        不支持的 hint 返回 None，由调用方 fallback 到父模型。
        """
        from ..configuration import get_process_config
        _HINT_MAP = {
            "haiku": "claude-haiku-4-5-20251001",
            "sonnet": get_process_config().model.model_id,  # 跟随进程默认模型
            "opus": "claude-opus-4-7",
            "inherit": None,          # 由调用方使用 rt.model
        }
        return _HINT_MAP.get(hint.lower().strip())

    # ── 同步派生子 Agent ─────────────────────────────────────────────────────────

    def spawn_child(self, prompt: str, agent_def=None, agent_name=None, prompt_version: str | None = None,
                    model_override: str | None = None, env_vars: dict[str, str] | None = None,
                    agent_id_override: str | None = None):
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
        # 延迟导入,避免循环依赖(Agent / AgentContext 在包 __init__ 中定义)
        from . import Agent, AgentContext
        from ccserver.builtins.tools.constants import CHILD_DISALLOWED_TOOLS, CHILD_DEFAULT_TOOLS, TEAMMATE_EXTRA_TOOLS

        rt = self._rt

        logger.debug(
            "spawn_child called | "
            "agent_name_param={!r} agent_def={} agent_def_name={!r}",
            agent_name,
            agent_def is not None,
            getattr(agent_def, "name", None) if agent_def else None,
        )

        # hook: subagent:spawn:before — 子代理派生前（observing，用于审计/记录）
        try:
            asyncio.get_running_loop()
            asyncio.create_task(rt.session.hooks.emit_void(
                "subagent:spawn:before",
                {
                    "prompt_preview": prompt[:200],
                    "agent_name": agent_name,
                    "agent_def": getattr(agent_def, "name", None) if agent_def else None,
                    "depth": rt.context.depth + 1,
                },
                rt._build_hook_ctx(),
            ))
        except RuntimeError:
            pass  # 无事件循环时（如单元测试）静默跳过

        # ── skills：子代理默认无 skill catalog，除非 agent_def.skills 显式指定 ──
        if agent_def is not None and agent_def.skills is not None:
            child_skills_override = agent_def.skills   # list[str]，可能是空列表
        else:
            child_skills_override = []                 # 不注入任何 skill catalog

        # 子代理的初始消息也要经过 prompt_engine.on_message() 处理
        initial_message = rt.prompt_engine.on_message(
            {"role": "user", "content": prompt}, rt.session, [],
            skills_override=child_skills_override,
        )
        # 子代理继承父代理的环境变量
        child_env_vars = dict(rt.context.env_vars)
        # agent_name 优先级：显式传入 > agent_def.name > None
        effective_name = agent_name or (agent_def.name if agent_def else None)
        logger.debug(
            "effective_name={!r} | agent_name_param={!r} agent_def_name={!r}",
            effective_name, agent_name, getattr(agent_def, "name", None) if agent_def else None,
        )
        child_context = AgentContext(
            name=effective_name,
            messages=[initial_message],
            depth=rt.context.depth + 1,
            parent_id=rt.context.agent_id,
            parent_name=rt.context.name,
            env_vars=child_env_vars,
        )
        if agent_id_override:
            child_context.agent_id = agent_id_override

        # ── 内置工具过滤（分层权限决策）────────────────────────────────────
        permissions = rt.session.config.permissions

        # 步骤 1：确定基础白名单
        if agent_def is not None and agent_def.tools is not None:
            allowed = set(agent_def.tools)
        else:
            allowed = set(CHILD_DEFAULT_TOOLS)
            if agent_def is not None and agent_def.is_teammate:
                allowed |= TEAMMATE_EXTRA_TOOLS

        # 步骤 2：应用 agent_def 黑名单
        if agent_def is not None and agent_def.disallowed_tools is not None:
            allowed -= set(agent_def.disallowed_tools)

        # 步骤 3：应用全局/项目权限黑名单
        allowed -= permissions.denied_tool_set()

        # 步骤 4：应用权限白名单约束（None 表示不限制）
        if permissions.allowed_tool_set() is not None:
            allowed &= permissions.allowed_tool_set()

        # 步骤 5：硬编码永久禁用（最后一道）
        allowed -= CHILD_DISALLOWED_TOOLS

        child_tools = {k: v for k, v in rt.tools.items() if k in allowed}
        disabled_child_tools = {k: v for k, v in rt.tools.items() if k not in child_tools}

        # ── system 注入 ───────────────────────────────────────────────────
        injected_system = None
        if agent_def is not None and agent_def.system:
            injected_system = agent_def.system

        # ── model ─────────────────────────────────────────────────────────
        # 优先级：model_override > agent_def.model > agent_def.model_hint > 继承父 agent
        child_model = model_override
        if not child_model and agent_def:
            if agent_def.model:
                child_model = agent_def.model
            elif agent_def.model_hint:
                child_model = self.resolve_model_hint(agent_def.model_hint)
        if not child_model:
            child_model = rt.model

        # 子 Agent 使用 BusEmitter，visibility=DONE_ONLY
        from ccserver.event_bus import _VISIBILITY_DONE_ONLY
        child_emitter = BusEmitter(
            bus=rt.session.event_bus,
            agent_id=child_context.agent_id,
            session_id=rt.session.id,
            visibility=_VISIBILITY_DONE_ONLY,
        )

        child = Agent(
            session=rt.session,
            adapter=rt.adapter,
            emitter=child_emitter,
            tools=child_tools,
            disabled_tools=disabled_child_tools,
            system=injected_system,
            context=child_context,
            model=child_model,
            round_limit=agent_def.round_limit if agent_def and agent_def.round_limit else rt.session.config.agent.sub_round_limit,
            limit_strategy=agent_def.limit_strategy if agent_def else "last_text",
            persist=False,
            prompt_version=prompt_version or rt.prompt_version,
            skills_override=child_skills_override,
            is_spawn=True,
            run_mode="auto",  # 子代理始终 auto
            env_vars=env_vars,
        )

        # ── MCP schemas 过滤后追加 ──
        if agent_def is not None and agent_def.mcp is not None:
            allowed_mcp = set(agent_def.mcp)
            child._schemas += [s for s in rt.session.mcp.schemas() if s["name"] in allowed_mcp]

        # 让 prompt_engine 对 schema 描述做后处理
        child._schemas = child.prompt_engine.patch_tool_schemas(child._schemas)
        child.recorder.schemas = child._schemas

        logger.debug(
            "spawn_child done | child_name={!r} child_aid={} child_context_name={!r} parent={}",
            child.context.name, child.aid_label, child_context.name, rt.aid_label,
        )

        # 发布 subagent_spawned 事件，供 monitor 追踪 Agent 树形关系
        try:
            asyncio.get_running_loop()
            asyncio.create_task(rt.session.event_bus.publish(AgentEvent(
                type=EventType.SUBAGENT_SPAWNED,
                agent_id=rt.context.agent_id,
                session_id=rt.session.id,
                sender_type=SenderType.AGENT,
                payload={
                    "parent_id": rt.context.agent_id,
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

    # ── 后台派生 ─────────────────────────────────────────────────────────────────

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
        同时通过 rt.emitter（父级 SSE/WebSocket emitter）推送 task_started / task_done。
        """
        from ccserver.tasks import AgentTaskState, generate_agent_id

        rt = self._rt
        # 0. 生成 Agent 任务 ID
        agent_task_id = generate_agent_id()

        # 1. 创建子 Agent（后台不需要实时流式）
        #    通过 rt.spawn_child 调用,而非 self.spawn_child:保持"spawn_background 使用
        #    父 Agent 的 spawn_child"这一既有契约(外部可 monkeypatch agent.spawn_child)。
        child = rt.spawn_child(
            prompt=prompt,
            agent_def=agent_def,
            agent_name=agent_name,
            model_override=model_override,
            env_vars=env_vars,
            agent_id_override=agent_id_override,
        )

        # 2. 创建 AgentTaskState 并注册到 Session
        resolved_name = agent_name or child.context.name or "unnamed"
        logger.debug(
            "spawn_background | resolved_name={!r} agent_name_param={!r} child.context.name={!r} task_id={}",
            resolved_name, agent_name, child.context.name, agent_task_id,
        )

        # 发布 subagent_spawned 事件
        asyncio.create_task(rt.session.event_bus.publish(AgentEvent(
            type=EventType.SUBAGENT_SPAWNED,
            agent_id=rt.context.agent_id,
            session_id=rt.session.id,
            sender_type=SenderType.AGENT,
            payload={
                "parent_id": rt.context.agent_id,
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
        # 3.1 同步 child.context.inbox 与 handle.inbox
        child.context.inbox = agent_task_state.inbox
        # 3.2 注入 agent_task_id，使 _loop() 的 PROGRESS 事件包含 task_id
        child.context.agent_task_id = agent_task_id
        rt.session.agent_tasks.register(agent_task_state)
        logger.debug(
            "AgentTask registered | agent_task_id={} agent_id={}",
            agent_task_id, child.context.agent_id[:8]
        )

        # 4. 创建 Handle
        handle = BackgroundAgentHandle(
            agent_id=child.context.agent_id,
            task_id=task_id,
            agent_task_id=agent_task_id,
            state=child.state,
            inbox=agent_task_state.inbox,
            agent_task_state=agent_task_state,
        )

        # 5. 通过父级 emitter 推送 task_started 事件（SSE/WebSocket）
        if hasattr(rt.emitter, "emit_task_started"):
            desc = agent_name or child.context.name or prompt[:80]
            rt.emitter.emit_task_started(
                task_id=agent_task_id,
                task_type="local_agent",
                description=desc,
                pid=None,
            )

        # 6. 启动终端事件监听协程
        child_agent_id = child.context.agent_id

        async def _watch_terminal_events():
            """
            订阅子 Agent 的 EventBus 终端事件，更新 AgentTaskState 并注入父 Agent 通知。
            """
            sub_id = f"terminal_{agent_task_id}"

            def filter_fn(e):
                return (
                    e.agent_id == child_agent_id
                    and e.type in {EventType.DONE, EventType.ERROR, EventType.CANCELLED}
                )

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
                # 后台 agent 完成标记：统一为 UnifiedMessage（与 context.messages 类型一致）。
                # Background-agent done marker: build as UnifiedMessage so context.messages
                # stays homogeneous; persist a dict via to_wire_dict (sqlite/mongo 安全)。
                from ccserver.messages import UnifiedMessage, UnifiedTextBlock, unified_message_to_wire
                done_message = UnifiedMessage(
                    role="system",
                    content=[UnifiedTextBlock(text=content)],
                    metadata={
                        "_ccserver_background_agent_done": True,
                        "agent_task_id": agent_task_id,
                        "agent_name": agent_name,
                    },
                )
                rt.context.messages.append(done_message)

                if rt.session.storage is not None:
                    rt.session.storage.append_message(rt.session.id, unified_message_to_wire(done_message))

                _hook_coro = rt.session.hooks.emit_void(
                    "background_agent:done",
                    {
                        "agent_task_id": agent_task_id,
                        "agent_name": agent_name,
                        "result": result,
                        "cancelled": cancelled,
                        "error": error,
                    },
                    rt._build_hook_ctx(),
                )
                if asyncio.iscoroutine(_hook_coro):
                    asyncio.create_task(_hook_coro)

                logger.debug(
                    "Parent notified (bus) | agent={} task_id={} cancelled={} error={}",
                    rt.aid_label, agent_task_id, cancelled, error,
                )

            async with rt.session.event_bus.subscribe(sub_id, filter_fn=filter_fn) as sub:
                while True:
                    event = await sub.get(timeout=30.0)
                    if event is None:
                        if handle._task is not None and handle._task.done():
                            break
                        continue

                    etype = event.type

                    if etype == EventType.DONE:
                        content = event.payload.get("content", "")
                        if agent_task_state is not None:
                            agent_task_state.mark_completed(result=content)
                        await _inject_done_notice(result=content)
                        logger.info(
                            "AgentTask done (bus) | agent_task_id={} agent_id={}",
                            agent_task_id, child_agent_id[:8],
                        )

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

        # P0-4：保存强引用防止 GC 回收 watcher task。
        # asyncio 文档要求：create_task 返回的 Task 必须保存在某处，
        # 否则事件循环只保留弱引用，GC 可能在任务完成前提前回收。
        # 把 watcher task 挂到 handle 上，生命周期与 handle 绑定。
        handle._watcher_task = asyncio.create_task(_watch_terminal_events())

        # 8. 启动后台 Agent 协程（不阻塞）
        async def _run_background():
            try:
                # 标记为 running（首次启动）
                if agent_task_state is not None:
                    agent_task_state.mark_running()

                await child.run(prompt)

                # ── Teammate 空闲循环：任务完成后进入 idle，等待新任务 ───────
                if is_teammate:
                    await child._set_phase("idle")
                    registry = rt.session.team_registry
                    if registry is not None:
                        from ccserver.team.models import TeamMemberState
                        registry.update_member_state_by_agent_id(
                            child.context.agent_id, TeamMemberState.IDLE
                        )
                        await rt.session.event_bus.publish(AgentEvent(
                            type=EventType.IDLE,
                            agent_id=child.context.agent_id,
                            session_id=rt.session.id,
                            sender_type=SenderType.AGENT,
                            payload={"completed_task_id": task_id},
                        ))
                        logger.info("Teammate idle | agent_id={}", child.context.agent_id)

                    idle_timeout = 60.0
                    while True:
                        try:
                            msg = await asyncio.wait_for(handle.inbox.get(), timeout=idle_timeout)
                        except asyncio.TimeoutError:
                            if handle._task is not None and handle._task.cancelled():
                                logger.info(
                                    "Teammate idle timeout+cancelled | agent_id={}",
                                    child.context.agent_id,
                                )
                                break
                            logger.debug("Teammate idle heartbeat | agent_id={}", child.context.agent_id)
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
                                # 新任务开始前重置 AgentTaskState 为 running
                                # （P0-5：防止 mark_completed 因状态不是 running 而警告）
                                if agent_task_state is not None:
                                    agent_task_state.status = AgentTaskStatus.RUNNING
                                    agent_task_state.result = None
                                    agent_task_state.error = None

                                await child.run(task_prompt)
                                await child._set_phase("idle")
                                if registry is not None:
                                    registry.update_member_state_by_agent_id(
                                        child.context.agent_id, TeamMemberState.IDLE
                                    )
                                    await rt.session.event_bus.publish(AgentEvent(
                                        type=EventType.IDLE,
                                        agent_id=child.context.agent_id,
                                        session_id=rt.session.id,
                                        sender_type=SenderType.AGENT,
                                        payload={"completed_task_id": msg.get("task_id")},
                                    ))

                            case MsgType.SHUTDOWN_REQUEST:
                                logger.info("Teammate shutdown | agent_id={}", child.context.agent_id)
                                await rt.session.event_bus.publish(AgentEvent(
                                    type=EventType.DONE,
                                    agent_id=child.context.agent_id,
                                    session_id=rt.session.id,
                                    sender_type=SenderType.AGENT,
                                    payload={"content": "[shutdown by lead]"},
                                ))
                                break

                            case MsgType.CHAT:
                                pass

                            case _:
                                logger.warning(
                                    "Teammate inbox unknown msg type ignored | agent_id={} msg={}",
                                    child.context.agent_id, msg,
                                )

            except asyncio.CancelledError:
                await rt.session.event_bus.publish(AgentEvent(
                    type=EventType.CANCELLED,
                    agent_id=child.context.agent_id,
                    session_id=rt.session.id,
                    sender_type=SenderType.AGENT,
                ))
            except Exception as e:
                await rt.session.event_bus.publish(AgentEvent(
                    type=EventType.ERROR,
                    agent_id=child.context.agent_id,
                    session_id=rt.session.id,
                    sender_type=SenderType.AGENT,
                    payload={"error": str(e)},
                ))
            finally:
                unregister_handle(handle.agent_id)
                if not agent_task_state.is_persistent:
                    rt.session.agent_tasks.evict(agent_task_id)

        handle._task = asyncio.create_task(_run_background())
        register_handle(handle)
        logger.info(
            "Background agent spawned | agent_task_id={} agent_id={} task_id={}",
            agent_task_id, handle.agent_id[:8], task_id
        )
        return handle

    # ── teammate 派生 ────────────────────────────────────────────────────────────

    async def spawn_teammate(
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

        rt = self._rt
        registry = rt.session.team_registry
        if registry is None:
            raise RuntimeError("Team feature is not enabled.")

        team = registry.get_team(team_name)
        if team is None:
            team = registry.create_team(team_name)
            from ccserver.team.dispatcher import TeamTaskDispatcher
            mailbox = TeamMailbox(team_name, rt.session.storage)
            dispatcher = TeamTaskDispatcher(
                team, mailbox,
                task_manager=rt.session.tasks,
                event_bus=rt.session.event_bus,
            )
            dispatcher.start()
            team._dispatcher = dispatcher
            team._mailbox = mailbox
        else:
            mailbox = getattr(team, "_mailbox", None)
            if mailbox is None:
                mailbox = TeamMailbox(team_name, rt.session.storage)
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
            from ccserver.managers.agents.manager import AgentDef
            effective_agent_def = AgentDef(
                name=name,
                description=f"Teammate {name} in team {team_name}",
                system=teammate_addendum,
                location=rt.session.project_root,
                is_teammate=True,
                is_team_capable=True,
            )

        handle = rt.spawn_background(
            prompt=prompt,
            agent_def=effective_agent_def,
            agent_name=name,
            model_override=model_override,
            agent_id_override=agent_id,
            is_teammate=True,
            is_persistent=True,  # teammate 默认永久驻留
        )

        agent_task = rt.session.agent_tasks.get_by_agent_id(agent_id)
        if agent_task is not None:
            pass

        # 设置父 agent（Lead 自己）的 _team_name，供后续 SendMessage 使用
        rt._team_name = team_name

        # 启动 EventBus SHUTDOWN 事件订阅者
        async def _watch_shutdown_events():
            def filter_fn(e):
                return e.type == EventType.SHUTDOWN and (
                    e.to_agent == agent_id or e.to_agent is None
                )
            sub_id = f"shutdown_{agent_id}"
            async with rt.session.event_bus.subscribe(sub_id, filter_fn=filter_fn) as sub:
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

        # 启动 Mailbox Poller
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
