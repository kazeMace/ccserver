"""Tests for Drama Engine persistent session storage."""

from __future__ import annotations

import pytest

from drama_engine.core.session.persistence import JsonSessionStore
from drama_engine.core.session.registry import SessionRegistry


@pytest.mark.asyncio
async def test_json_store_restores_session_tokens_and_events(tmp_path) -> None:
    """重建 registry 后应恢复 session、玩家 token/link 和事件回放。"""
    store = JsonSessionStore(tmp_path / "store")
    registry = SessionRegistry(store=store)
    runtime = await registry.create_session(
        game_id="persist_game",
        script_path="scripts/persist.yaml",
        seat_ids=["Player_1", "Player_2"],
        human_seat_ids={"Player_1"},
        params={"use_runner": False},
        metadata={"name": "持久化测试"},
    )
    token = registry.token_service.token_for_seat(runtime.session.session_id, "Player_1")
    assert token is not None

    runtime.event_store.append_public({"kind": "message", "text": "public"})
    runtime.event_store.append_private("Player_1", {"kind": "secret", "text": "private"})
    runtime.memory_store.append("group_chat.summary", {"text": "memory survives restart"})
    request = runtime.action_service.create_request(
        "Player_1",
        "请选择",
        kind="vote",
        candidates=["A", "B"],
        metadata={"scene_display_name": "投票"},
    )
    registry._save_to_store()

    restored = SessionRegistry(store=store)
    restored_runtime = await restored.get_session(runtime.session.session_id)
    restored_token = restored.token_service.token_for_seat(runtime.session.session_id, "Player_1")

    assert restored_runtime.session.game_id == "persist_game"
    assert restored_runtime.session.metadata["name"] == "持久化测试"
    assert restored_runtime.player_links["Player_1"] == runtime.player_links["Player_1"]
    assert restored_token == token
    assert restored.token_service.validate(token).seat_id == "Player_1"
    assert restored_runtime.event_store.public_backlog()[0]["text"] == "public"
    assert restored_runtime.event_store.private_backlog("Player_1")[0]["text"] == "private"
    assert restored_runtime.memory_store.latest("group_chat.summary") == {"text": "memory survives restart"}
    restored_request = restored_runtime.action_service.get_current_request("Player_1")
    assert restored_request.request_id == request.request_id
    assert restored_request.candidates == ["A", "B"]

    submission = await restored_runtime.action_service.submit(
        seat_id="Player_1",
        source="human",
        data={"vote": "A"},
        text="A",
    )

    assert submission.validated is True
    assert restored_runtime.action_service.get_current_request("Player_1") is None


@pytest.mark.asyncio
async def test_running_session_restores_as_assigned(tmp_path) -> None:
    """运行中的 asyncio task 不持久化，重启恢复时应降级为 assigned。"""
    store = JsonSessionStore(tmp_path / "store")
    registry = SessionRegistry(store=store)
    runtime = await registry.create_session(
        game_id="running_restore",
        script_path="scripts/running.yaml",
        seat_ids=["Player_1"],
        params={"use_runner": False},
    )
    await registry.assign_session(runtime.session.session_id)
    await registry.start_session(runtime.session.session_id)
    assert runtime.session.status == "running"

    restored = SessionRegistry(store=store)
    restored_runtime = await restored.get_session(runtime.session.session_id)

    assert restored_runtime.session.status == "assigned"
    assert restored_runtime.session.metadata["restored_from_status"] == "running"
    assert "not persisted" in restored_runtime.session.metadata["restore_note"]
