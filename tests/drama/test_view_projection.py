"""View projector boundary tests."""

import pytest

from drama_engine.core.session.registry import SessionRegistry
from drama_engine.core.ports.views import BaseViewProjector
from drama_engine.core.session.view_projection import (
    SocialViewProjector,
    build_host_snapshot,
    build_player_snapshot,
    build_public_snapshot,
)


@pytest.mark.asyncio
async def test_social_view_projector_matches_compat_snapshot_functions() -> None:
    """SocialViewProjector 应输出完整 snapshot。"""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="view",
        script_path="scripts/view.yaml",
        seat_ids=["Player_1"],
        human_seat_ids={"Player_1"},
        params={"use_runner": False},
    )
    runtime.event_store.append_public({"kind": "public_note"})
    runtime.event_store.append_private("Player_1", {"kind": "private_note"})
    runtime.session.seats["Player_1"].role_snapshot = "guard"
    runtime.session.seats["Player_1"].alive_snapshot = True
    runtime.action_service.create_request("Player_1", "请选择", kind="vote", candidates=["A"])
    projector = SocialViewProjector()

    assert isinstance(projector, BaseViewProjector)
    assert projector.host_snapshot(runtime).to_dict() == build_host_snapshot(runtime).to_dict()
    assert projector.public_snapshot(runtime).to_dict() == build_public_snapshot(runtime).to_dict()
    assert projector.player_snapshot(runtime, "Player_1").to_dict() == build_player_snapshot(
        runtime,
        "Player_1",
    ).to_dict()
    assert projector.project(runtime, audience="player", seat_id="Player_1")[0]["role_card"]["role"] == "guard"
