"""会话进度追踪器（ProgressTracker）。

ProgressTracker 是 SessionControl 的一个组件，负责把「会话进行到哪了」持续写入
`SessionState.progress`，并推进 timeline cursor。它只更新会话过程状态，不碰游戏事实。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.game_instance.state import ProgressState, SessionState

logger = logging.getLogger(__name__)


class ProgressTracker:
    """维护 SessionState.progress 与 timeline cursor。

    interactive_session runtime 在推进 flow/scene/round 时调用本追踪器，
    使会话进度与事件/消息/动作 cursor 保持一致，供视图和回滚使用。
    """

    def __init__(self, session_state: SessionState) -> None:
        """绑定到一局会话状态。"""
        assert session_state is not None, "session_state 不能为空"
        self._session = session_state

    @property
    def progress(self) -> ProgressState:
        """返回当前进度状态。"""
        return self._session.progress

    def record_progress(
        self,
        current_state: str | None = None,
        current_scene: str | None = None,
        round: int | None = None,
        turn: int | None = None,
        phase: str | None = None,
        actor: str | None = None,
    ) -> ProgressState:
        """更新当前进度；只覆盖显式传入的字段。

        参数为 None 表示保持原值，方便调用方只更新关心的维度。
        """
        progress = self._session.progress
        if current_state is not None:
            progress.current_state = current_state
        if current_scene is not None:
            progress.current_scene = current_scene
        if round is not None:
            assert round >= 0, "round 不能为负数"
            progress.round = round
        if turn is not None:
            assert turn >= 0, "turn 不能为负数"
            progress.turn = turn
        if phase is not None:
            progress.phase = phase
        if actor is not None:
            progress.actor = actor
        logger.debug(
            "[ProgressTracker] 进度更新 session=%s state=%s scene=%s round=%s",
            self._session.session_id,
            progress.current_state,
            progress.current_scene,
            progress.round,
        )
        return progress

    def set_cursors(
        self,
        event_cursor: int | None = None,
        message_cursor: int | None = None,
        action_cursor: int | None = None,
    ) -> None:
        """设置 timeline cursor；只覆盖显式传入的值。"""
        if event_cursor is not None:
            assert event_cursor >= 0, "event_cursor 不能为负数"
            self._session.event_cursor = event_cursor
        if message_cursor is not None:
            assert message_cursor >= 0, "message_cursor 不能为负数"
            self._session.message_cursor = message_cursor
        if action_cursor is not None:
            assert action_cursor >= 0, "action_cursor 不能为负数"
            self._session.action_cursor = action_cursor

    def snapshot(self) -> dict[str, Any]:
        """返回进度与 cursor 的可序列化快照，供 checkpoint 使用。"""
        return {
            "progress": self._session.progress.to_dict(),
            "event_cursor": self._session.event_cursor,
            "message_cursor": self._session.message_cursor,
            "action_cursor": self._session.action_cursor,
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        """从 checkpoint 快照恢复进度与 cursor。"""
        assert isinstance(snapshot, dict), "snapshot 必须是 dict"
        self._session.progress = ProgressState.from_dict(snapshot.get("progress") or {})
        self._session.event_cursor = int(snapshot.get("event_cursor") or 0)
        self._session.message_cursor = int(snapshot.get("message_cursor") or 0)
        self._session.action_cursor = int(snapshot.get("action_cursor") or 0)


__all__ = ["ProgressTracker"]
