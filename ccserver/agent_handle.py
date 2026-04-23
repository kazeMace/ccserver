"""
agent_handle — BackgroundAgentHandle：后台 Agent 的句柄。

用于外部控制后台 Agent：查询状态、发送消息、取消任务。
同时持有 AgentTaskState，通过 emitter 推送 task_started/task_done 事件。

事件流
────────────────────────────────────────────────────────────────────────────
spawn_background() → BackgroundAgentHandle
    ├─ 生成 agent_task_id = "a" + uuid[:8]
    ├─ 创建 AgentTaskState 并注册到 session.agent_tasks
    ├─ emit_task_started(parent_emitter, agent_task_id, ...)
    └─ asyncio.create_task(_forward_agent_events())

_forward_agent_events():
    - 监听 outbox 队列（child.emitter 推送的 done/cancelled/error 事件）
    - 收到 done → emit_task_done(status="completed", result=...)
    - 收到 cancelled → emit_task_done(status="cancelled")
    - 收到 error   → emit_task_done(status="failed", error=...)
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from loguru import logger


if TYPE_CHECKING:
    from ccserver.tasks import AgentTaskState
    from ccserver.emitters.base import BaseEmitter


def generate_agent_task_id() -> str:
    """
    生成唯一的 Agent 任务 ID。

    格式："a" + uuid 前 8 位。与 ShellTaskState 的 "b" 前缀共同构成任务 ID 空间。
    """
    return f"a{uuid.uuid4().hex[:8]}"


# ─── BackgroundAgentHandle ────────────────────────────────────────────────────

@dataclass
class BackgroundAgentHandle:
    """
    后台 Agent 的句柄，用于外部控制和事件通知。

    Attributes
    ──────────
    agent_id : str
        后台 Agent 实例的唯一标识（AgentContext.agent_id）。

    task_id : str | None
        可选绑定的持久化任务 ID（TaskManager 中的 Todo/Task ID）。

    agent_task_id : str
        本模块生成的 Agent 任务 ID（格式 "a" + uuid[:8]）。
        用于 SSE/WebSocket 事件推送和 HTTP API 查询。

    state : AgentState
        引用 Agent.state，可实时查询运行阶段（phase）。

    inbox : asyncio.Queue
        外部向此 Agent 发送消息的队列。外部通过 handle.send_message() 注入消息。

    outbox : asyncio.Queue
        此 Agent 产出的事件队列。
        内部由 QueueEmitter 填充，外部可监听以获取 Agent 产出。

    agent_task_state : AgentTaskState | None
        Agent 任务的状态记录，注册到 session.agent_tasks。
        可通过 session.agent_tasks.get(agent_task_id) 查询。

    _task : asyncio.Task | None
        内部运行的 asyncio.Task（后台协程）。
    """
    agent_id: str
    task_id: Optional[str]
    agent_task_id: str = field(default_factory=generate_agent_task_id)
    state: "AgentState" = None  # type: ignore[assignment]
    inbox: asyncio.Queue = field(default_factory=asyncio.Queue)
    outbox: asyncio.Queue = field(default_factory=asyncio.Queue)
    _task: Optional[asyncio.Task] = None
    agent_task_state: "AgentTaskState | None" = field(default=None, repr=False)

    async def cancel(self) -> None:
        """
        取消后台 Agent，并等待其真正结束。

        通过 asyncio.Task.cancel() 发送 CancelledError，
        由 child.run() 中的 await 点触发退出。
        同时更新 AgentTaskState 状态。
        """
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            if self.state is not None:
                self.state.phase = "cancelled"
            if self.agent_task_state is not None:
                self.agent_task_state.mark_cancelled()
            logger.info(
                "BackgroundAgentHandle: cancelled | agent_id={} agent_task_id={}",
                self.agent_id[:8], self.agent_task_id
            )

    async def send_message(self, payload: dict) -> None:
        """发送消息给后台 Agent，注入其 inbox。"""
        await self.inbox.put(payload)

    def get_output(self) -> str:
        """
        获取最终输出（阻塞等待 done 事件）。

        注意：此方法会阻塞当前线程，直到后台 Agent 结束。
        推荐使用 await self.wait_done()。
        """
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self._wait_done())

    async def _wait_done(self) -> str:
        """异步等待 done 事件，返回 Agent 的最终输出。"""
        while True:
            event = await self.outbox.get()
            etype = event.get("type")
            if etype == "done":
                return event.get("content", "")
            if etype in ("error", "cancelled"):
                return event.get("error", "") if etype == "error" else ""


# ─── Agent 事件转发 ──────────────────────────────────────────────────────────

async def _poll_agent_progress(
    handle: BackgroundAgentHandle,
    parent_emitter: "BaseEmitter",
    interval: float = 5.0,
) -> None:
    """
    定期向后台 Agent 注入 status_request，从中收集 progress 事件并转发。

    这是 Path B 的核心：外部轮询协程通过 inbox 注入请求，
    agent 的 _loop() 在每轮开始时响应，写入 outbox，
    此协程从 outbox 读取并推送给 SSE/WebSocket 客户端。

    Args:
        handle:          后台 Agent 的句柄（含 inbox 和 outbox）。
        parent_emitter:  父级 emitter，用于推送 task_progress 事件。
        interval:        轮询间隔（秒），默认 5 秒。
    """
    try:
        while True:
            # 等待 interval 秒
            await asyncio.sleep(interval)

            # 检查 Agent 是否仍在运行
            if handle.state is not None and handle.state.phase not in (
                "running", "llm_calling", "tool_executing"
            ):
                break  # Agent 已结束，退出轮询

            # 向 Agent inbox 注入 status_request
            try:
                handle.inbox.put_nowait({"type": "status_request"})
            except asyncio.QueueFull:
                # inbox 满了，跳过本轮
                logger.debug(
                    "Agent progress poll skipped (inbox full) | agent_id={}",
                    handle.agent_id[:8]
                )
                continue

            # 等待 progress 响应（最多等 interval 秒）
            try:
                event = await asyncio.wait_for(handle.outbox.get(), timeout=interval)
            except asyncio.TimeoutError:
                continue

            etype = event.get("type")
            if etype == "progress":
                # 透传 progress 事件
                progress_info = {
                    "round_num": event.get("round_num", 0),
                    "max_rounds": event.get("max_rounds", 0),
                    "phase": event.get("phase", "running"),
                    "current_tool": event.get("current_tool"),
                }
                await parent_emitter.emit_task_progress(
                    task_id=handle.agent_task_id,
                    status="running",
                    output="",
                    progress=progress_info,
                )
                logger.debug(
                    "AgentTask progress forwarded | agent_task_id={} round={}/{}",
                    handle.agent_task_id,
                    event.get("round_num", 0),
                    event.get("max_rounds", 0),
                )
            elif etype in ("done", "cancelled", "error"):
                # done 类事件已由 forward_agent_events 处理，忽略
                break
    except asyncio.CancelledError:
        logger.debug("Agent progress poller cancelled | agent_id={}", handle.agent_id[:8])
    except Exception as e:
        logger.error(
            "Agent progress poller exception | agent_id={} error={}",
            handle.agent_id[:8], e
        )


async def forward_agent_events(
    handle: BackgroundAgentHandle,
    parent_emitter: "BaseEmitter",
) -> None:
    """
    监听后台 Agent 的 outbox 队列，将所有事件转换为对应事件推送。

    事件路由：
      progress  → emit_task_progress（透传，不终结）
      done      → emit_task_done(status=completed) + 终结
      cancelled → emit_task_done(status=cancelled) + 终结
      error     → emit_task_done(status=failed) + 终结

    此协程在 spawn_background() 中作为 asyncio.create_task() 启动，
    与 Agent.run() 和 _poll_agent_progress() 并发运行。
    当收到终端事件（done/cancelled/error）后协程退出。

    Args:
        handle:          后台 Agent 的句柄（含 outbox 队列和 agent_task_state）。
        parent_emitter: 父级 emitter（SSEEmitter / WSEmitter），
                         用于向客户端推送 task_done / task_progress 事件。
    """
    try:
        while True:
            event = await handle.outbox.get()
            etype = event.get("type")

            if etype == "progress":
                # 透传 progress 事件
                progress_info = {
                    "round_num": event.get("round_num", 0),
                    "max_rounds": event.get("max_rounds", 0),
                    "phase": event.get("phase", "running"),
                    "current_tool": event.get("current_tool"),
                }
                await parent_emitter.emit_task_progress(
                    task_id=handle.agent_task_id,
                    status="running",
                    output=event.get("content", ""),
                    progress=progress_info,
                )
                continue  # 继续监听，progress 不终结

            if etype == "done":
                content = event.get("content", "")
                if handle.agent_task_state is not None:
                    handle.agent_task_state.mark_completed(result=content)
                await parent_emitter.emit_task_done(
                    task_id=handle.agent_task_id,
                    status="completed",
                    output=content[:50_000] if content else "",
                    exit_code=None,
                    reason=None,
                )
                logger.info(
                    "AgentTask done | agent_task_id={} agent_id={} output_len={}",
                    handle.agent_task_id, handle.agent_id[:8], len(content)
                )
                break  # 终端事件，退出循环

            elif etype == "cancelled":
                if handle.agent_task_state is not None:
                    handle.agent_task_state.mark_cancelled()
                await parent_emitter.emit_task_done(
                    task_id=handle.agent_task_id,
                    status="cancelled",
                    output="",
                    exit_code=None,
                    reason="cancelled by parent",
                )
                logger.info(
                    "AgentTask cancelled | agent_task_id={} agent_id={}",
                    handle.agent_task_id, handle.agent_id[:8]
                )
                break

            elif etype == "error":
                error_msg = event.get("error", "unknown error")
                if handle.agent_task_state is not None:
                    handle.agent_task_state.mark_failed(error=error_msg)
                await parent_emitter.emit_task_done(
                    task_id=handle.agent_task_id,
                    status="failed",
                    output="",
                    exit_code=None,
                    reason=error_msg[:500],
                )
                logger.warning(
                    "AgentTask failed | agent_task_id={} agent_id={} error={}",
                    handle.agent_task_id, handle.agent_id[:8], error_msg[:100]
                )
                break

    except asyncio.CancelledError:
        logger.debug(
            "forward_agent_events cancelled | agent_task_id={}",
            handle.agent_task_id
        )
    except Exception as e:
        logger.error(
            "forward_agent_events exception | agent_task_id={} error={}",
            handle.agent_task_id, e
        )
