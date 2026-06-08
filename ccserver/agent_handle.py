"""
agent_handle — BackgroundAgentHandle：后台 Agent 的句柄。

用于外部控制后台 Agent：查询状态、发送消息、取消任务。

事件流（EventBus 重构后）
────────────────────────────────────────────────────────────────────────────
spawn_background() → BackgroundAgentHandle
    ├─ 生成 agent_task_id = "a" + uuid[:8]
    ├─ 创建 AgentTaskState 并注册到 session.agent_tasks
    ├─ emit_task_started(parent_emitter, agent_task_id, ...)
    └─ asyncio.create_task(_watch_terminal_events())   # 订阅 EventBus 终端事件

_watch_terminal_events()（在 agent.py 中定义，作为闭包）:
    - 订阅 Session EventBus，filter_fn = lambda e: e.agent_id == child_agent_id
      and e.type in {DONE, ERROR, CANCELLED}
    - 收到 DONE      → mark_completed + 注入父 Agent 完成通知消息
    - 收到 ERROR     → mark_failed    + 注入父 Agent 完成通知消息
    - 收到 CANCELLED → mark_cancelled + 注入父 Agent 完成通知消息

注意：PROGRESS / token / tool_start 等中间事件不再由本模块处理，
      改由 SSEEmitter / WSEmitter 直接订阅 EventBus 完成推送。
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from loguru import logger


if TYPE_CHECKING:
    from ccserver.tasks import AgentTaskState
    from ccserver.agent import AgentState


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
        Team Lead 通过此队列向 Teammate 发送 new_task / shutdown_request 等消息。

    agent_task_state : AgentTaskState | None
        Agent 任务的状态记录，注册到 session.agent_tasks。
        可通过 session.agent_tasks.get(agent_task_id) 查询。

    _task : asyncio.Task | None
        内部运行的 asyncio.Task（后台协程）。
    """
    agent_id: str
    task_id: Optional[str]
    agent_task_id: str = field(default_factory=generate_agent_task_id)
    state: Optional["AgentState"] = None
    inbox: asyncio.Queue = field(default_factory=asyncio.Queue)
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

    def is_running(self) -> bool:
        """返回后台 Agent 的 asyncio.Task 是否仍在运行。"""
        return self._task is not None and not self._task.done()
