"""Checkpoint + Rollback 测试（架构文档 §7）。"""

from __future__ import annotations

import asyncio

import pytest

from drama_engine.core.engine import SetAttr, State, StateWriter, Vocabulary
from drama_engine.core.game_instance.factory import GameInstanceRegistry
from drama_engine.core.game_instance.session_control import SessionControl
from drama_engine.core.game_instance.snapshots import SnapshotManager
from drama_engine.core.game_instance.rollback import RollbackManager
from drama_engine.core.game_instance.state import SessionState
from drama_engine.core.runtime.interactive_session.patch.journal import PatchJournal
from drama_engine.core.session.events import SessionEventStore
from drama_engine.core.session.factory import _build_action_request_service

_SCRIPT = "drama_engine/scripts/interactive_session/story/text_adventure_interactive.yaml"
_SCRIPT_UNDERCOVER = "drama_engine/scripts/interactive_session/deduction/who_is_undercover.yaml"


def test_state_full_snapshot_restore_roundtrip() -> None:
    """engine.State.full_snapshot/restore 应完整还原属性与关系。"""
    state = State(Vocabulary(frozenset(), frozenset(), frozenset(), frozenset()))
    state.register_entity("GAME", {"round": 1})
    state.register_entity("Player_1", {"alive": True})
    writer = StateWriter(state)
    writer.apply(SetAttr("GAME", "round", 2))

    snap = state.full_snapshot()
    # 破坏状态
    writer.apply(SetAttr("GAME", "round", 99))
    writer.apply(SetAttr("Player_1", "alive", False))

    state.restore(snap)
    assert state.get_attr("GAME", "round") == 2
    assert state.get_attr("Player_1", "alive") is True


def test_patch_journal_snapshot_restore_roundtrip() -> None:
    """PatchJournal.snapshot/restore 应还原记录。"""
    journal = PatchJournal()
    journal.append("push_schedule", {"mode": "openchat"})
    snap = journal.snapshot()
    journal.append("pop_schedule", {})
    assert len(journal.all()) == 2

    journal.restore(snap)
    assert len(journal.all()) == 1
    assert journal.all()[0].patch_type == "push_schedule"


def _make_control_with_state():
    """构造 SessionControl + 一个独立 State/journal，供 manager 单测。"""
    session = SessionState(game_id="g", script_path="p.yaml", seat_ids=["Player_1"])
    event_store = SessionEventStore(session.session_id)
    action_service = _build_action_request_service(session)
    control = SessionControl(session, event_store, action_service)
    state = State(Vocabulary(frozenset(), frozenset(), frozenset(), frozenset()))
    state.register_entity("GAME", {"round": 1})
    journal = PatchJournal()
    return control, state, journal


def test_snapshot_manager_and_rollback_manager_restore_game_state() -> None:
    """SnapshotManager 建点后改状态，RollbackManager 应恢复 GameState 与事件。"""
    control, state, journal = _make_control_with_state()
    writer = StateWriter(state)

    manager = SnapshotManager(
        session_control=control,
        state_provider=lambda: state,
        journal_provider=lambda: journal,
        clock=lambda: "2026-07-06T00:00:00+00:00",
    )
    rollback = RollbackManager(
        session_control=control,
        state_provider=lambda: state,
        journal_provider=lambda: journal,
    )

    control.append_public({"kind": "session_started"})
    control.progress.record_progress(current_scene="intro", round=1)
    checkpoint = manager.create_checkpoint("before_change")

    # 推进：改游戏状态、加事件、加 patch
    writer.apply(SetAttr("GAME", "round", 5))
    control.append_public({"kind": "later_event"})
    journal.append("push_schedule", {"mode": "openchat"})
    control.progress.record_progress(current_scene="later", round=5)

    rollback.restore(checkpoint, policy="branch")

    # GameState 恢复
    assert state.get_attr("GAME", "round") == 1
    # patch journal 恢复为空
    assert journal.all() == []
    # 进度恢复
    assert control.progress.progress.current_scene == "intro"
    # 事件回放恢复到 checkpoint（1 条 public）+ rollback_applied（host）
    assert len(control.public_backlog()) == 1
    host_kinds = [e.get("kind") for e in control.host_backlog()]
    assert "rollback_applied" in host_kinds


@pytest.mark.asyncio
async def test_game_instance_checkpoint_and_rollback_end_to_end() -> None:
    """通过 GameInstance 建 checkpoint、改状态、回滚，GameState 应恢复。"""
    registry = GameInstanceRegistry(store=None, load_existing=False)
    instance = await registry.create_instance(
        game_id="story",
        script_path=_SCRIPT,
        seat_ids=["Player_1"],
        params={"dry_run": True, "use_runner": True},
    )
    await instance.assign()  # runner 就绪，game_state 可用

    state = instance._current_game_state()
    assert state is not None
    StateWriter(state).apply(SetAttr("GAME", "round", 1))

    summary = instance.checkpoint("before_start")
    assert summary["reason"] == "before_start"
    assert instance.rollback_points()[0]["checkpoint_id"] == summary["checkpoint_id"]

    # 改游戏状态后回滚
    StateWriter(state).apply(SetAttr("GAME", "round", 42))
    await instance.rollback_to(summary["checkpoint_id"])

    restored = instance._current_game_state()
    assert restored.get_attr("GAME", "round") == 1


@pytest.mark.asyncio
async def test_who_is_undercover_playable_rollback_sample() -> None:
    """可回滚 playable sample（文档 §18 step 10）：谁是卧底建点→推进→回滚→状态还原。"""
    registry = GameInstanceRegistry(store=None, load_existing=False)
    instance = await registry.create_instance(
        game_id="who_is_undercover",
        script_path=_SCRIPT_UNDERCOVER,
        seat_ids=[f"Player_{i}" for i in range(1, 7)],
        params={"dry_run": True, "use_runner": True},
    )
    await instance.assign()
    state = instance._current_game_state()

    # 在开局身份分配后建 checkpoint（玩家应带 role 与默认 alive）
    summary = instance.checkpoint("after_assign")
    assert state.get_attr("Player_6", "role") == "undercover"
    assert state.get_attr("Player_1", "alive") is True

    # 推进：模拟一次错误出局（把卧底之外的人投出）
    StateWriter(state).apply(SetAttr("Player_1", "alive", False))
    StateWriter(state).apply(SetAttr("GAME", "last_vote_target", "Player_1"))
    assert state.get_attr("Player_1", "alive") is False

    # 回滚到开局：错误出局被撤销
    await instance.rollback_to(summary["checkpoint_id"])
    restored = instance._current_game_state()
    assert restored.get_attr("Player_1", "alive") is True
    assert restored.get_attr("GAME", "last_vote_target") is None
    # 事件流留有 rollback 记录
    assert any(e.get("kind") == "rollback_applied" for e in instance.timeline("host"))
