"""SessionControl 与 ProgressTracker 测试。"""

from __future__ import annotations

import pytest

from drama_engine.core.game_instance.progress import ProgressTracker
from drama_engine.core.game_instance.session_control import SessionControl
from drama_engine.core.game_instance.state import SessionState
from drama_engine.core.session.events import SessionEventStore
from drama_engine.core.session.factory import _build_action_request_service


def _make_control() -> SessionControl:
    """构造一个绑定内存 store 的 SessionControl。"""
    session = SessionState(
        game_id="g",
        script_path="p.yaml",
        seat_ids=["Player_1", "Player_2"],
    )
    event_store = SessionEventStore(session.session_id)
    action_service = _build_action_request_service(session)
    return SessionControl(session, event_store, action_service)


def test_progress_tracker_updates_only_given_fields() -> None:
    """record_progress 只覆盖显式传入字段，其余保持原值。"""
    session = SessionState(game_id="g", script_path="p.yaml", seat_ids=["Player_1"])
    tracker = ProgressTracker(session)

    tracker.record_progress(current_scene="intro", round=1)
    assert session.progress.current_scene == "intro"
    assert session.progress.round == 1
    assert session.progress.turn == 0

    tracker.record_progress(turn=2)
    assert session.progress.current_scene == "intro"  # 保持
    assert session.progress.turn == 2


def test_progress_tracker_snapshot_roundtrip() -> None:
    """进度快照可完整恢复。"""
    session = SessionState(game_id="g", script_path="p.yaml", seat_ids=["Player_1"])
    tracker = ProgressTracker(session)
    tracker.record_progress(current_state="main", current_scene="vote", round=3, phase="day")
    tracker.set_cursors(event_cursor=5, message_cursor=4, action_cursor=2)

    snap = tracker.snapshot()
    # 破坏当前进度后再恢复
    tracker.record_progress(current_scene="night", round=9)
    tracker.set_cursors(event_cursor=99)
    tracker.restore(snap)

    assert session.progress.current_scene == "vote"
    assert session.progress.round == 3
    assert session.event_cursor == 5
    assert session.action_cursor == 2


def test_session_control_event_append_advances_cursor() -> None:
    """SessionControl 追加事件后应推进 event cursor。"""
    control = _make_control()
    control.append_public({"kind": "session_started"})
    control.append_host({"kind": "act", "actor": "Player_1", "text": "hi"})

    assert control.session_state.event_cursor == len(control.host_backlog())
    assert control.session_state.event_cursor == 2


def test_session_control_snapshot_restore_roundtrip() -> None:
    """SessionControl 会话过程快照可恢复事件与进度。"""
    control = _make_control()
    control.append_public({"kind": "session_started"})
    control.progress.record_progress(current_scene="intro", round=1)
    snap = control.snapshot()

    control.append_public({"kind": "extra_event"})
    control.progress.record_progress(current_scene="later", round=5)
    control.restore(snap)

    assert control.progress.progress.current_scene == "intro"
    assert control.progress.progress.round == 1
    # 事件回放恢复到快照时刻（只有 1 条 public）
    assert len(control.public_backlog()) == 1


@pytest.mark.asyncio
async def test_session_control_pending_actions_delegates() -> None:
    """pending_actions 委托给 action_service。"""
    control = _make_control()
    assert control.pending_actions() == []
