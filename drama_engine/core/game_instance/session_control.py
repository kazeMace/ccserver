"""会话控制中心（SessionControl）。

SessionControl 是局内会话过程的统一收口（架构文档 §5）。它不负责具体游戏规则，
只负责会话过程中的消息、动作、事件、进度、快照和回滚数据。

设计取向（避免过度设计）：
- 复用现有成熟组件，而不是重写：事件/消息 timeline 复用 `SessionEventStore`，
  动作 timeline 复用 `ActionRequestService`，持久化复用 `JsonSessionStore`。
- SessionControl 额外提供三样现有组件没有、但 GameInstance / 回滚需要的能力：
  1. `ProgressTracker`：把会话进度写入 SessionState。
  2. 统一的 append/backlog/cursor 视图，隐藏底层三个 store 的细节。
  3. 会话过程快照 / 恢复接口，供 SnapshotManager / RollbackManager（阶段4）调用。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.game_instance.progress import ProgressTracker
from drama_engine.core.game_instance.state import SessionState
from drama_engine.core.session.events import SessionEventStore

logger = logging.getLogger(__name__)


class SessionControl:
    """局内会话控制中心。

    组合：
      - session_state：会话过程状态（SessionState）。
      - event_store：public/host/private 事件与消息 timeline（SessionEventStore）。
      - action_service：动作请求/提交 timeline（ActionRequestService 或其 router）。
      - progress：ProgressTracker，维护 SessionState.progress 与 cursor。
    """

    def __init__(
        self,
        session_state: SessionState,
        event_store: SessionEventStore,
        action_service: Any,
    ) -> None:
        """绑定会话状态与底层 store。"""
        assert session_state is not None, "session_state 不能为空"
        assert event_store is not None, "event_store 不能为空"
        assert action_service is not None, "action_service 不能为空"
        assert session_state.session_id == event_store.session_id, "event_store session_id 不一致"
        self.session_state = session_state
        self.event_store = event_store
        self.action_service = action_service
        self.progress = ProgressTracker(session_state)
        logger.info("[SessionControl] 初始化 session=%s", session_state.session_id)

    # ---- 事件 / 消息 timeline（append-only）----

    def append_public(self, event: dict[str, Any]) -> None:
        """追加公开事件/消息，并推进 event cursor。"""
        self.event_store.append_public(dict(event))
        self._advance_event_cursor()

    def append_host(self, event: dict[str, Any]) -> None:
        """追加主持人事件，并推进 event cursor。"""
        self.event_store.append_host(dict(event))
        self._advance_event_cursor()

    def append_private(self, seat_id: str, event: dict[str, Any]) -> None:
        """追加指定 seat 的私密事件，并推进 event cursor。"""
        assert seat_id, "seat_id 不能为空"
        self.event_store.append_private(seat_id, dict(event))
        self._advance_event_cursor()

    def public_backlog(self) -> list[dict[str, Any]]:
        """返回公开事件回放。"""
        return self.event_store.public_backlog()

    def host_backlog(self) -> list[dict[str, Any]]:
        """返回主持人视角回放。"""
        return self.event_store.host_backlog()

    def private_backlog(self, seat_id: str) -> list[dict[str, Any]]:
        """返回指定 seat 的私密回放。"""
        return self.event_store.private_backlog(seat_id)

    def _advance_event_cursor(self) -> None:
        """走 SessionControl 的 append 路径时即时推进 cursor（快捷路径）。

        注意：cursor 的**权威值**由 sync_cursors() 从 timeline 派生，不依赖所有事件
        都经过本方法——因为 GameRuntime 等更低层会直接写 event_store。见 sync_cursors。
        """
        self.sync_cursors()

    def sync_cursors(self) -> None:
        """从 event timeline 派生 event/message cursor（M6/M5.3）。

        cursor 是**派生值**而非写路径记账：无论事件从 SessionControl 还是 GameRuntime
        等更低层写入，这里都按当前 timeline 长度重算，保证快照/视图里的 cursor 真实。
          - event_cursor：host 可见 timeline 长度（public + host-only）。
          - message_cursor：公开消息流长度（public timeline），即对外可见的消息位置。
        """
        self.session_state.event_cursor = len(self.event_store.host_backlog())
        self.session_state.message_cursor = len(self.event_store.public_backlog())

    # ---- 动作 timeline ----

    def pending_actions(self) -> list[dict[str, Any]]:
        """返回当前 pending 动作摘要。"""
        return self.action_service.pending_summary()

    async def submit_action(
        self,
        seat_id: str,
        source: str,
        data: dict[str, Any] | None,
        text: str,
    ) -> Any:
        """提交一个动作到当前 pending request，并推进 action cursor。"""
        assert seat_id, "seat_id 不能为空"
        assert source, "source 不能为空"
        submission = await self.action_service.submit(
            seat_id=seat_id,
            source=source,
            data=data,
            text=text,
        )
        if submission is not None:
            self.session_state.action_cursor += 1
        return submission

    # ---- 会话过程快照 / 恢复（供阶段4 checkpoint 使用）----

    def snapshot(self) -> dict[str, Any]:
        """返回会话过程的可序列化快照。

        包含：session 状态、事件回放、动作 store、进度与 cursor。游戏事实（GameState）
        不在此处，由 GameInstance 在 checkpoint 时单独收集。
        """
        # 快照前从 timeline 重算 cursor，确保捕获到低层直接写入的事件（M6/M5.3）。
        self.sync_cursors()
        return {
            "session": self.session_state.to_dict(),
            "events": self.event_store.dump(),
            "actions": self.action_service.service_action.dump()
            if hasattr(self.action_service, "service_action")
            else {},
            "progress": self.progress.snapshot(),
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        """从会话过程快照恢复事件回放、动作 store 与进度。

        注意：session 元数据（座位/生命周期）由调用方决定是否整体替换；这里只恢复
        timeline 与进度，保证与 checkpoint 一致。
        """
        assert isinstance(snapshot, dict), "snapshot 必须是 dict"
        event_data = snapshot.get("events")
        if isinstance(event_data, dict):
            self.event_store.load(event_data)
        action_data = snapshot.get("actions")
        if isinstance(action_data, dict) and hasattr(self.action_service, "service_action"):
            self.action_service.service_action.load(action_data)
        progress_data = snapshot.get("progress")
        if isinstance(progress_data, dict):
            self.progress.restore(progress_data)
        logger.info("[SessionControl] 已从快照恢复 session=%s", self.session_state.session_id)


__all__ = ["SessionControl"]
