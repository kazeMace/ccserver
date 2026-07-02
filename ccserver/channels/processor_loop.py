"""
channels/processor_loop — EventBus → Processor 驱动循环管理器。

职责：为每个 Session 维护一个后台协程，
      从 EventBus 取事件后按 visibility 过滤，分发到 OutputTarget.Processor。
从 ChannelGateway 拆出，单一职责：只管事件驱动循环的生命周期。
"""

import asyncio
from typing import TYPE_CHECKING

from loguru import logger

from .output_target import OutputTarget
from ccserver.event_bus import AgentEvent, EventType, _VISIBILITY_HIDDEN, _VISIBILITY_DONE_ONLY

if TYPE_CHECKING:
    from ccserver.session import Session


async def _dispatch_event_to_processor(target: OutputTarget, event: AgentEvent) -> None:
    """
    将单个 AgentEvent 按 visibility 规则分发到 OutputTarget 的 Processor。

    visibility 过滤规则：
      HIDDEN    → 丢弃所有事件
      DONE_ONLY → 只处理 DONE / ERROR，忽略 TOKEN 等中间事件
      FULL      → 处理所有事件

    ask_user / permission_req 特殊处理：
      事件 payload 中携带 asyncio.Future，Processor 负责在用户响应后 set_result()。
      多个 OutputTarget 收到同一 future 时，只有第一个 set_result() 有效（Future 幂等）。
    """
    vis = event.visibility
    if vis == _VISIBILITY_HIDDEN:
        return

    t = event.type

    if t == EventType.TOKEN:
        if vis != _VISIBILITY_DONE_ONLY:
            await target.processor.on_token(event.payload.get("token", ""), event)

    elif t == EventType.DONE:
        content = event.payload.get("content", "")
        if content:
            await target.processor.on_done(content, event)

    elif t == EventType.ERROR:
        await target.processor.on_error(event.payload.get("error", "unknown error"), event)

    elif t == EventType.ASK_USER:
        future = event.payload.get("future")
        questions = event.payload.get("questions", [])
        if future is not None and not future.done():
            def make_answer_cb(f):
                def answer_cb(text: str):
                    if not f.done():
                        f.set_result(text)
                return answer_cb
            await target.processor.on_ask_user(questions, make_answer_cb(future))

    elif t == EventType.PERMISSION_REQ:
        future = event.payload.get("future")
        tool_name = event.payload.get("tool_name", "")
        tool_input = event.payload.get("tool_input", {})
        if future is not None and not future.done():
            def make_grant_cb(f):
                def grant_cb(approved: bool):
                    if not f.done():
                        f.set_result(approved)
                return grant_cb
            await target.processor.on_permission_request(tool_name, tool_input, make_grant_cb(future))


class ProcessorLoopManager:
    """
    管理每个 Session 对应的 EventBus → Processor 驱动循环。

    ensure(session_id)：幂等启动，已存在且运行中的循环不重复创建。
    cleanup(session_id)：取消并移除指定 session 的循环。
    shutdown()：取消所有循环。

    Attributes:
        _session_manager: SessionManager，用于按 ID 取 Session
        _tasks:           session_id -> asyncio.Task
    """

    def __init__(self, session_manager):
        self._session_manager = session_manager
        self._tasks: dict[str, asyncio.Task] = {}

    async def ensure(self, session_id: str) -> None:
        """
        确保指定 session 的驱动循环已启动（幂等）。

        已有运行中的循环直接返回，任务退出后可重新创建。
        """
        existing = self._tasks.get(session_id)
        if existing is not None and not existing.done():
            return

        session = self._session_manager.get(session_id)
        if session is None:
            logger.warning(
                "ProcessorLoopManager.ensure: session not found | id={}",
                session_id[:8],
            )
            return

        task = asyncio.create_task(self._run_loop(session))
        self._tasks[session_id] = task
        logger.debug("Processor loop started | session={}", session_id[:8])

    async def cleanup(self, session_id: str) -> None:
        """取消并移除指定 session 的驱动循环。"""
        task = self._tasks.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        logger.debug("Processor loop cleaned up | session={}", session_id[:8])

    async def shutdown(self) -> None:
        """取消所有驱动循环。"""
        for session_id, task in list(self._tasks.items()):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._tasks.clear()
        logger.info("ProcessorLoopManager shutdown complete")

    async def _run_loop(self, session: "Session") -> None:
        """
        EventBus 监听协程（每个 Session 一个）。

        持续从 EventBus 获取事件，分发到所有 output_targets 的 Processor。
        收到 DONE / ERROR / CANCELLED 事件后，调用 on_turn_end() 通知 Processor。
        """
        session_id = session.id
        subscriber_id = f"proc_loop_{session_id[:8]}"
        try:
            async with session.event_bus.subscribe(subscriber_id) as sub:
                while True:
                    try:
                        event = await sub.get(timeout=5.0)
                    except asyncio.CancelledError:
                        break
                    if event is None:
                        continue

                    # 取当前 output_targets（每次取最新，支持动态更新）
                    targets = list(session.output_targets)
                    for target in targets:
                        try:
                            await _dispatch_event_to_processor(target, event)
                        except Exception as e:
                            logger.error(
                                "Processor dispatch error | session={} channel={} err={}",
                                session_id[:8], target.channel_id, e,
                            )

                    # 轮次结束信号：通知所有 Processor，然后清空当前轮次目标
                    if event.type in (EventType.DONE, EventType.ERROR, EventType.CANCELLED):
                        for target in targets:
                            try:
                                await target.processor.on_turn_end()
                            except Exception as e:
                                logger.error(
                                    "on_turn_end error | session={} channel={} err={}",
                                    session_id[:8], target.channel_id, e,
                                )
                        # P1-1：轮次结束后清空 output_targets（当前轮次），
                        # 防止旧 target 的 answer_future / permission_future 跨轮次泄漏。
                        # default_output_targets 保留：供 Cron / Background Agent 推送用。
                        session.output_targets = []
                        logger.debug(
                            "output_targets cleared after turn end | session={}",
                            session_id[:8],
                        )

        except Exception as e:
            logger.error(
                "Processor loop error | session={} err={}",
                session_id[:8], e,
            )
