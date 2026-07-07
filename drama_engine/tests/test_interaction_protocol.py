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
    # ReplyOption 完整字段（§3）
    opt = rr["options"][0]
    assert set(opt.keys()) == {"id", "text", "desc", "disabled", "disabled_reason", "meta"}
    assert rr["timeout_ms"] == 30000
    assert proj.project_request(None) is None


def test_projector_message_and_option_full_shape() -> None:
    """InteractionMessage.sender 与 ReplyOption 字段与文档 §2/§3 完全一致。"""
    proj = InteractionProjector()
    msg = proj.project_event({"seq": 1, "type": "interactive_message", "actor": "A", "text": "x"})
    assert set(msg["sender"].keys()) == {"kind", "id", "name", "emoji", "role", "dead"}
    assert set(msg["body"].keys()) == {"text", "style", "cards"}
    # game_pack 可通过 metadata.option_meta 注入 vote 的 emoji/票数（§9 开放键）
    class Req:
        request_id = "r"; kind = "vote"; cue = "c"; candidates = ["Player_1"]; schema = None
        metadata = {"option_meta": {"Player_1": {"emoji": "🐺", "count": 3}}}
        allow_resubmit = False; timeout_seconds = None
    rr = proj.project_request(Req())
    assert rr["options"][0]["meta"] == {"emoji": "🐺", "count": 3}


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


def test_projection_profile_enriches_widget_and_props():
    """M-profile：ProjectionProfile 按 scene 富化 ReplyRequest 的 widget/props（开放键）。"""
    from types import SimpleNamespace
    from drama_engine.core.interaction.profile import ProjectionProfile

    profile = ProjectionProfile(
        widget_by_scene={"wolf_kill": "vote:night_kill"},
        props_by_scene={"wolf_kill": {"show_teammate_votes": True}},
    )
    projector = InteractionProjector()
    request = SimpleNamespace(
        request_id="r1", kind="vote", cue="选择击杀目标", scene_name="wolf_kill",
        candidates=["Player_6", "Player_7"], schema=None, timeout_seconds=30,
        metadata={}, allow_resubmit=False,
    )
    reply = projector.project_request(request, profile=profile)
    assert reply["primitive"] == "vote"
    assert reply["widget"] == "vote:night_kill"
    assert reply["props"] == {"show_teammate_votes": True}


def test_projection_profile_absent_keeps_open_keys_none():
    """无 profile 时开放键为 None（走 primitive 保底），封闭键仍在。"""
    from types import SimpleNamespace
    projector = InteractionProjector()
    request = SimpleNamespace(
        request_id="r2", kind="vote", cue="投票", scene_name="day_vote",
        candidates=["A"], schema=None, timeout_seconds=None, metadata={}, allow_resubmit=False,
    )
    reply = projector.project_request(request)
    assert reply["primitive"] == "vote"
    assert reply["widget"] is None
    assert reply["props"] is None


def test_projector_metadata_hints_produce_choice_or_text_confirm_multi():
    """metadata 提示驱动 choice_or_text / confirm / multi_choice（偏差修正）。"""
    from types import SimpleNamespace
    projector = InteractionProjector()

    # free_input 提示 → choice_or_text，并带 free_input 块
    r1 = projector.project_request(SimpleNamespace(
        request_id="a", kind="choose", cue="回应她", scene_name="talk",
        candidates=["greet", "shy"], schema=None, timeout_seconds=None,
        metadata={"free_input": True}, allow_resubmit=False,
    ))
    assert r1["primitive"] == "choice_or_text"
    assert r1["free_input"] is not None

    # confirm 提示 → presentation=confirm
    r2 = projector.project_request(SimpleNamespace(
        request_id="b", kind="choose", cue="继续", scene_name="s",
        candidates=["ok"], schema=None, timeout_seconds=None,
        metadata={"confirm": True}, allow_resubmit=False,
    ))
    assert r2["presentation"] == "confirm"

    # multi 提示 → multi_choice + min/max_select 生效
    r3 = projector.project_request(SimpleNamespace(
        request_id="c", kind="choose", cue="选圈子", scene_name="s",
        candidates=["red", "blue", "green"], schema=None, timeout_seconds=None,
        metadata={"multi": True, "min_select": 1, "max_select": 2}, allow_resubmit=False,
    ))
    assert r3["primitive"] == "multi_choice"
    assert r3["min_select"] == 1 and r3["max_select"] == 2


def test_projector_card_has_variant_key():
    """RichCard 始终带 variant 键（降级链 card.variant→kind）。"""
    projector = InteractionProjector()
    msg = projector.project_event({
        "seq": 1, "type": "interactive_publication", "view_kind": "clue",
        "view_variant": "clue:public", "data": {"title": "线索"},
    })
    cards = msg["body"]["cards"]
    assert cards and cards[0]["kind"] == "clue" and cards[0]["variant"] == "clue:public"
