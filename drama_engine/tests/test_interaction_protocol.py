"""interaction.v1 投影器与 REST 端点测试（docs/interaction_protocol_design.md）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from drama_engine.application.catalog import GameCatalog
from drama_engine.core.interaction.projector import InteractionProjector
from drama_engine.core.session.registry import SessionRegistry
from drama_engine.service.server.app import create_app

_UNDERCOVER = "drama_engine/scripts/interactive_session/deduction/who_is_undercover.yaml"


def test_projector_event_to_interaction_message() -> None:
    """内部事件 → InteractionMessage：封闭 role + 完整 §2 字段。"""
    proj = InteractionProjector()
    msg = proj.project_event(
        {"seq": 5, "session_id": "s", "type": "interactive_message", "actor": "Player_2", "text": "hi", "scope": "public"},
        self_seat="Player_1",
    )
    assert msg["seq"] == 5
    assert msg["role"] == "dialogue"
    assert msg["sender"]["kind"] == "agent"  # Player_2 不是自己
    assert msg["body"]["text"] == "hi"
    assert msg["scope"] == "public"
    # 自己发的消息标记 human
    mine = proj.project_event({"seq": 6, "type": "interactive_message", "actor": "Player_1", "text": "me"}, self_seat="Player_1")
    assert mine["sender"]["kind"] == "human"


def test_projector_disclosure_is_secret_role() -> None:
    """验人结果类事件投影为 secret role。"""
    proj = InteractionProjector()
    msg = proj.project_event({"seq": 1, "type": "interactive_disclosure", "text": "P2 是狼"})
    assert msg["role"] == "secret"


def test_projector_request_to_reply_request() -> None:
    """ActionRequest → ReplyRequest：动作 kind → 封闭 primitive。"""
    proj = InteractionProjector()

    class FakeRequest:
        request_id = "r1"
        kind = "vote"
        cue = "投票放逐"
        candidates = ["Player_1", "Player_2"]
        schema = None
        metadata: dict = {}
        allow_resubmit = False
        timeout_seconds = 30.0

    rr = proj.project_request(FakeRequest())
    assert rr is not None
    assert rr["primitive"] == "vote"
    assert rr["request_id"] == "r1"
    assert len(rr["options"]) == 2
    assert rr["timeout_ms"] == 30000
    assert proj.project_request(None) is None


def test_projector_build_inbox_attaches_pending_and_status() -> None:
    """build_inbox 按 after 增量 + 挂 pending + 映射 status。"""
    proj = InteractionProjector()
    events = [
        {"seq": 1, "type": "session_started", "text": "开始"},
        {"seq": 2, "type": "interactive_message", "actor": "A", "text": "hi"},
    ]

    class FakeRequest:
        request_id = "r1"
        kind = "speak"
        cue = "发言"
        candidates = None
        schema = None
        metadata: dict = {}
        allow_resubmit = False
        timeout_seconds = None

    inbox = proj.build_inbox(events, after=1, pending_request=FakeRequest(), status="running", self_seat="A")
    assert [m["seq"] for m in inbox["messages"]] == [2]  # 只取 seq>1
    assert inbox["pending"]["primitive"] == "text"
    assert inbox["messages"][-1]["reply_request"] is not None
    assert inbox["status"] == "running"
    # 无 pending 且 running → waiting_others
    inbox2 = proj.build_inbox(events, after=0, pending_request=None, status="running")
    assert inbox2["status"] == "waiting_others"


@pytest.mark.asyncio
async def test_game_instance_inbox_reply_view() -> None:
    """GameInstance.inbox/view 产出协议对象，三受众可用。"""
    from drama_engine.core.game_instance.factory import GameInstanceRegistry

    registry = GameInstanceRegistry(store=None, load_existing=False)
    inst = await registry.create_instance(
        game_id="u",
        script_path=_UNDERCOVER,
        seat_ids=[f"Player_{i}" for i in range(1, 7)],
        params={"dry_run": True, "use_runner": True},
    )
    await inst.assign()
    pub = inst.inbox("public", after=0)
    assert "messages" in pub and "cursor" in pub and "status" in pub
    host = inst.inbox("host", after=0)
    assert isinstance(host["messages"], list)
    view = inst.view("player:Player_1")
    assert view["seat_id"] == "Player_1"
    assert isinstance(view["players"], list)


def test_rest_inbox_reply_view_endpoints() -> None:
    """/inbox /reply /view REST 端点经 GameInstance 工作。"""
    app = create_app(registry=SessionRegistry(), catalog=GameCatalog(scripts_root="missing"))
    with TestClient(app) as client:
        created = client.post(
            "/api/sessions",
            json={
                "game_id": "u",
                "script_path": _UNDERCOVER,
                "seat_ids": [f"Player_{i}" for i in range(1, 7)],
                "params": {"dry_run": True, "use_runner": True},
            },
        )
        sid = created.json()["session_id"]
        # inbox（未 start 也应返回结构）
        inbox = client.get(f"/api/sessions/{sid}/inbox?seat=public&after=0")
        assert inbox.status_code == 200
        assert "messages" in inbox.json()
        # view
        view = client.get(f"/api/sessions/{sid}/view?seat=host")
        assert view.status_code == 200
        assert "players" in view.json()
        # reply 缺 seat_id → 400
        bad = client.post(f"/api/sessions/{sid}/reply", json={"request_id": "x"})
        assert bad.status_code == 400
