"""Tests for Drama Engine FastAPI service skeleton."""

from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

from drama_engine.application.catalog import GameCatalog
from drama_engine.core.session.registry import SessionRegistry
from drama_engine.service.server.app import create_app


def test_create_session_via_api_with_explicit_script_path() -> None:
    """API 应能创建 session 并返回玩家链接。"""
    app = create_app(registry=SessionRegistry(), catalog=GameCatalog(scripts_root="missing"))
    client = TestClient(app)

    response = client.post(
        "/api/sessions",
        json={
            "game_id": "custom_game",
            "script_path": "scripts/custom.yaml",
            "seat_ids": ["Player_1", "Player_2"],
            "human_seat_ids": ["Player_1"],
            "params": {"total_players": 2, "use_runner": False},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["game_id"] == "custom_game"
    assert payload["status"] == "lobby"
    assert payload["seat_count"] == 2
    assert payload["human_seat_count"] == 1
    assert "Player_1" in payload["player_links"]


def test_two_api_sessions_are_listed_separately() -> None:
    """API 创建的两局应该都能在 session list 中看到。"""
    app = create_app(registry=SessionRegistry(), catalog=GameCatalog(scripts_root="missing"))
    client = TestClient(app)

    for name in ["first", "second"]:
        response = client.post(
            "/api/sessions",
            json={
                "game_id": name,
                "script_path": f"scripts/{name}.yaml",
                "seat_ids": ["Player_1"],
                "human_seat_ids": ["Player_1"],
                "params": {"use_runner": False},
            },
        )
        assert response.status_code == 200

    response = client.get("/api/sessions")
    assert response.status_code == 200
    sessions = response.json()
    assert len(sessions) == 2
    assert {session["game_id"] for session in sessions} == {"first", "second"}
    assert sessions[0]["session_id"] != sessions[1]["session_id"]


def test_session_lifecycle_api_is_session_scoped() -> None:
    """assign/start/pause/resume 应只影响目标 session。"""
    app = create_app(registry=SessionRegistry(), catalog=GameCatalog(scripts_root="missing"))
    client = TestClient(app)

    first = client.post(
        "/api/sessions",
        json={
            "game_id": "first",
            "script_path": "scripts/first.yaml",
            "seat_ids": ["Player_1"],
            "params": {"use_runner": False},
        },
    ).json()
    second = client.post(
        "/api/sessions",
        json={
            "game_id": "second",
            "script_path": "scripts/second.yaml",
            "seat_ids": ["Player_1"],
            "params": {"use_runner": False},
        },
    ).json()

    first_id = first["session_id"]
    second_id = second["session_id"]

    assert client.post(f"/api/sessions/{first_id}/assign").status_code == 200
    assert client.post(f"/api/sessions/{first_id}/start").status_code == 200
    assert client.post(f"/api/sessions/{first_id}/pause").status_code == 200
    assert client.post(f"/api/sessions/{first_id}/resume").status_code == 200

    first_after = client.get(f"/api/sessions/{first_id}").json()
    second_after = client.get(f"/api/sessions/{second_id}").json()

    assert first_after["status"] == "running"
    assert second_after["status"] == "lobby"


def test_api_restart_reassigns_existing_session_without_conflict() -> None:
    """Dashboard 重新开始应清局并重新发牌，不能再次调用 assign 导致 409。"""
    app = create_app(registry=SessionRegistry(), catalog=GameCatalog(scripts_root="missing"))

    with TestClient(app) as client:
        payload = client.post(
            "/api/sessions",
            json={
                "game_id": "restartable",
                "script_path": "scripts/restartable.yaml",
                "seat_ids": ["Player_1", "Player_2"],
                "human_seat_ids": ["Player_1"],
                "params": {"use_runner": False},
            },
        ).json()
        session_id = payload["session_id"]

        assert client.post(f"/api/sessions/{session_id}/assign").status_code == 200
        assert client.post(f"/api/sessions/{session_id}/start").status_code == 200

        restart = client.post(f"/api/sessions/{session_id}/restart")
        assert restart.status_code == 200

        restarted = client.get(f"/api/sessions/{session_id}").json()
        assert restarted["session_id"] == session_id
        assert restarted["status"] == "assigned"
        assert restarted["human_seat_count"] == 1

        seats = client.get(f"/api/sessions/{session_id}/seats").json()
        assert seats[0]["controller_type"] == "human"
        assert seats[0]["join_link"]
        assert all(seat["role_snapshot"] is None for seat in seats)


def test_api_assign_start_real_runner_dry_run() -> None:
    """API assign/start 应能接入真实 YAML runner 并完成 dry-run 游戏。"""
    app = create_app(registry=SessionRegistry(), catalog=GameCatalog(scripts_root="missing"))

    with TestClient(app) as client:
        response = client.post(
            "/api/sessions",
            json={
                "game_id": "werewolf_v1_guard",
                "script_path": "drama_engine/core/scripts/werewolf_v1_guard.yaml",
                "seat_ids": [f"Player_{index}" for index in range(1, 13)],
                "params": {"dry_run": True, "use_runner": True},
            },
        )
        assert response.status_code == 200
        session_id = response.json()["session_id"]

        assert client.post(f"/api/sessions/{session_id}/assign").status_code == 200
        assigned = client.get(f"/api/sessions/{session_id}").json()
        assert assigned["status"] == "assigned"

        assert client.post(f"/api/sessions/{session_id}/start").status_code == 200

        import time

        final = None
        for _ in range(120):
            final = client.get(f"/api/sessions/{session_id}").json()
            if final["status"] in {"ended", "failed"}:
                break
            time.sleep(0.1)

        assert final is not None
        assert final["status"] == "ended"


def test_player_reconnect_backlog_is_token_scoped() -> None:
    """玩家重连快照应通过 token 定位 session + seat。"""
    app = create_app(registry=SessionRegistry(), catalog=GameCatalog(scripts_root="missing"))

    with TestClient(app) as client:
        response = client.post(
            "/api/sessions",
            json={
                "game_id": "private_event_game",
                "script_path": "scripts/private.yaml",
                "seat_ids": ["Player_1", "Player_2"],
                "human_seat_ids": ["Player_1", "Player_2"],
                "params": {"use_runner": False},
            },
        )
        payload = response.json()
        session_id = payload["session_id"]
        token_1 = payload["player_links"]["Player_1"].split("token=", 1)[1]
        token_2 = payload["player_links"]["Player_2"].split("token=", 1)[1]

        runtime = app.state.registry._sessions[session_id]
        runtime.event_store.append_private("Player_1", {"kind": "secret", "text": "one"})
        runtime.event_store.append_private("Player_2", {"kind": "secret", "text": "two"})

        first = client.get(f"/api/player/reconnect?token={token_1}").json()
        second = client.get(f"/api/player/reconnect?token={token_2}").json()

        assert first["seat_id"] == "Player_1"
        assert second["seat_id"] == "Player_2"
        assert first["backlog"][0]["kind"] == "secret"
        assert first["backlog"][0]["text"] == "one"
        assert first["backlog"][0]["seat_id"] == "Player_1"
        assert first["backlog"][0]["audience"] == "private"
        assert first["backlog"][0]["session_id"] == session_id
        assert second["backlog"][0]["kind"] == "secret"
        assert second["backlog"][0]["text"] == "two"
        assert second["backlog"][0]["seat_id"] == "Player_2"
        assert second["backlog"][0]["audience"] == "private"
        assert second["backlog"][0]["session_id"] == session_id


def test_frontend_assets_and_host_page_are_served() -> None:
    """创建页和 Host 游戏页应拆成两个页面并提供前端资源。"""
    app = create_app(registry=SessionRegistry(), catalog=GameCatalog(scripts_root="missing"))
    client = TestClient(app)

    create_resp = client.get("/")
    assert create_resp.status_code == 200
    assert "创建狼人杀房间" in create_resp.text
    assert "scriptSelect" in create_resp.text
    assert "humanCountInput" in create_resp.text
    assert "humanCountSelect" not in create_resp.text
    assert "观战模式" in create_resp.text
    assert "真实 Agent" in create_resp.text
    assert "预女猎守局12人" in create_resp.text
    assert "live_viewer.js" not in create_resp.text
    assert "create.js" in create_resp.text
    assert "进入 Dashboard" in create_resp.text
    assert "进入导演页" not in create_resp.text

    host_resp = client.get("/host/sessions/example-session")
    assert host_resp.status_code == 200
    assert "drama engine dashboard" in host_resp.text
    assert "createRoomBtn" not in host_resp.text
    assert "创建狼人杀房间" not in host_resp.text
    assert "live_viewer.js" in host_resp.text

    js_resp = client.get("/frontend/live_viewer.js")
    assert js_resp.status_code == 200
    assert "serviceContext" in js_resp.text
    assert client.get("/frontend/create.js").status_code == 200
    css_resp = client.get("/frontend/live_viewer.css")
    assert css_resp.status_code == 200
    config = client.get("/api/frontend/config").json()
    assert config["title"]


def test_create_page_can_create_guard_preset_room_and_return_player_links() -> None:
    """首页创建页使用当前唯一 preset 时，应返回真人玩家链接。"""
    app = create_app(registry=SessionRegistry(), catalog=GameCatalog(scripts_root="missing"))
    client = TestClient(app)

    response = client.post(
        "/api/sessions",
        json={
            "game_id": "werewolf_v1_12p_guard",
            "script_path": "drama_engine/core/scripts/werewolf_v1_guard.yaml",
            "seat_ids": [f"Player_{index}" for index in range(1, 13)],
            "human_seat_ids": ["Player_1", "Player_2", "Player_3"],
            "params": {"total_players": 12, "werewolf_count": 4, "dry_run": False, "use_runner": True},
            "metadata": {
                "preset_path": "drama_engine/core/presets/werewolf_v1_12p_guard.preset.yaml",
                "preset_label": "预女猎守局12人",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["seat_count"] == 12
    assert payload["human_seat_count"] == 3
    assert payload["player_links"]["Player_1"].startswith("http://testserver/player?token=")
    assert payload["player_links"]["Player_2"].startswith("http://testserver/player?token=")
    assert payload["host_url"].startswith("http://testserver/host/sessions/")
    assert payload["viewer_url"].startswith("http://testserver/viewer/sessions/")
    assert payload["metadata"]["preset_label"] == "预女猎守局12人"
    assert payload["metadata"]
    runtime = app.state.registry._sessions[payload["session_id"]]
    assert runtime.session.params["dry_run"] is False


def test_seats_api_includes_join_link_for_host_frontend() -> None:
    """Host 前端需要 seats 返回 join_link 以打开玩家链接。"""
    app = create_app(registry=SessionRegistry(), catalog=GameCatalog(scripts_root="missing"))
    client = TestClient(app)

    response = client.post(
        "/api/sessions",
        json={
            "game_id": "link_game",
            "script_path": "scripts/link.yaml",
            "seat_ids": ["Player_1"],
            "human_seat_ids": ["Player_1"],
            "params": {"use_runner": False},
        },
    )
    session_id = response.json()["session_id"]
    seats = client.get(f"/api/sessions/{session_id}/seats").json()
    assert seats[0]["seat_id"] == "Player_1"
    assert seats[0]["join_link"].startswith("http://testserver/player?token=")


def test_public_urls_respect_forwarded_headers() -> None:
    """ngrok/反向代理场景下，后端应按 forwarded headers 生成外部链接。"""
    app = create_app(registry=SessionRegistry(), catalog=GameCatalog(scripts_root="missing"))
    client = TestClient(app)

    response = client.post(
        "/api/sessions",
        headers={
            "x-forwarded-proto": "https",
            "x-forwarded-host": "demo.ngrok-free.app",
        },
        json={
            "game_id": "ngrok_game",
            "script_path": "scripts/ngrok.yaml",
            "seat_ids": ["Player_1"],
            "human_seat_ids": ["Player_1"],
            "params": {"use_runner": False},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["player_links"]["Player_1"].startswith("https://demo.ngrok-free.app/player?token=")
    assert payload["host_url"].startswith("https://demo.ngrok-free.app/host/sessions/")

    session_id = payload["session_id"]
    seats = client.get(
        f"/api/sessions/{session_id}/seats",
        headers={
            "x-forwarded-proto": "https",
            "x-forwarded-host": "demo.ngrok-free.app",
        },
    ).json()
    assert seats[0]["join_link"].startswith("https://demo.ngrok-free.app/player?token=")


def test_view_snapshot_apis_and_player_pages() -> None:
    """player/public/host view API 与页面资源应该可用。"""
    app = create_app(registry=SessionRegistry(), catalog=GameCatalog(scripts_root="missing"))
    client = TestClient(app)

    response = client.post(
        "/api/sessions",
        json={
            "game_id": "view_game",
            "script_path": "scripts/view.yaml",
            "seat_ids": ["Player_1"],
            "human_seat_ids": ["Player_1"],
            "params": {"use_runner": False},
        },
    )
    payload = response.json()
    session_id = payload["session_id"]
    token = payload["player_links"]["Player_1"].split("token=", 1)[1]

    assert client.get(f"/api/sessions/{session_id}/view/host").status_code == 200
    assert client.get(f"/api/sessions/{session_id}/view/public").status_code == 200
    player_view = client.get(f"/api/player/view?token={token}")
    assert player_view.status_code == 200
    assert player_view.json()["seat_id"] == "Player_1"
    assert client.get(f"/player?token={token}").status_code == 200
    assert client.get(f"/viewer/sessions/{session_id}").status_code == 200
    assert client.get("/frontend/player.js").status_code == 200
    assert client.get("/frontend/viewer.js").status_code == 200
    assert client.get("/frontend/simple.css").status_code == 200


def test_player_frontend_action_panel_below_timeline_and_sheriff_options() -> None:
    """玩家页当前操作应在我的消息下方；上警操作应显示语义选项而不是候选玩家。"""
    html_path = "drama_engine/service/frontend/player.html"
    js_path = "drama_engine/service/frontend/player.js"
    css_path = "drama_engine/service/frontend/simple.css"
    html = __import__("pathlib").Path(html_path).read_text(encoding="utf-8")
    js = __import__("pathlib").Path(js_path).read_text(encoding="utf-8")
    css = __import__("pathlib").Path(css_path).read_text(encoding="utf-8")

    assert html.index("我的消息") < html.index("当前操作")
    assert "我选择上警" in js
    assert "我选择不上警" in js
    assert js.index('cue.includes("上警")') < js.index("candidates.length")
    assert "使用自定义输入" in js
    assert "customActionValue" in js
    assert "补充发言" in js
    assert "actionReason" in js
    assert "custom-action-value" in css



def test_player_frontend_keeps_action_form_while_polling_same_request() -> None:
    """玩家页轮询到同一 pending request 时不能重建输入表单，否则输入会被清空。"""
    js_path = "drama_engine/service/frontend/player.js"
    js = __import__("pathlib").Path(js_path).read_text(encoding="utf-8")

    assert "let renderedActionKey" in js
    assert "function actionRenderKey" in js
    assert "request:${requestId}" in js
    assert 'renderedActionKey === nextActionKey && el("actionForm")' in js
    assert "不要重建表单" in js
    assert 'renderedActionKey = ""' in js



@pytest.mark.asyncio
async def test_player_input_api_submits_to_runner_action_service() -> None:
    """真人玩家提交应路由到当前 ActionRequestService。"""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="human_input_test",
        script_path="drama_engine/core/scripts/werewolf_v1_guard.yaml",
        seat_ids=["Player_1"],
        human_seat_ids={"Player_1"},
        params={"use_runner": False},
    )
    token = registry.token_service.token_for_seat(runtime.session.session_id, "Player_1")
    request = runtime.action_service.create_request("Player_1", "请选择", candidates=["A", "B"])

    app = create_app(registry=registry, catalog=GameCatalog(scripts_root="missing"))
    client = TestClient(app)
    response = client.post(
        "/api/player/input",
        json={"token": token, "data": {"vote": "A"}, "text": "A"},
    )

    assert response.status_code == 200
    assert response.json()["request_id"] == request.request_id
    assert runtime.action_service.get_current_request("Player_1") is None


@pytest.mark.asyncio
async def test_action_service_prefixes_human_submission_text_with_scene() -> None:
    """真人玩家提交应自动补【玩家｜场景】前缀，已补前缀时不重复补。"""
    from drama_engine.core.ports.timeout import ACTION_KIND_SPEECH
    from drama_engine.core.session.actions import ActionRequestService

    service = ActionRequestService(session_id="prefix-session")
    service.create_request(
        seat_id="Player_9",
        cue="请发言",
        kind=ACTION_KIND_SPEECH,
        metadata={"scene_name": "sheriff-vote", "scene_display_name": "警长投票"},
    )

    first_submission = await service.submit(
        seat_id="Player_9",
        source="human",
        data={"text": "我投给 3 号"},
        text="我投给 3 号",
    )

    assert first_submission.text == "【Player_9｜警长投票】我投给 3 号"

    service.create_request(
        seat_id="Player_9",
        cue="请发言",
        kind=ACTION_KIND_SPEECH,
        metadata={"scene_name": "sheriff-vote", "scene_display_name": "警长投票"},
    )
    second_submission = await service.submit(
        seat_id="Player_9",
        source="human",
        data={"text": "【Player_9｜警长投票】我已带前缀"},
        text="【Player_9｜警长投票】我已带前缀",
    )

    assert second_submission.text == "【Player_9｜警长投票】我已带前缀"



def test_moderator_control_apis_update_seats_and_links() -> None:
    """主持人控制 API 应能切换控制方式、设置真人数量、重置链接。"""
    app = create_app(registry=SessionRegistry(), catalog=GameCatalog(scripts_root="missing"))
    client = TestClient(app)

    payload = client.post(
        "/api/sessions",
        json={
            "game_id": "moderator_game",
            "script_path": "scripts/moderator.yaml",
            "seat_ids": ["Player_1", "Player_2", "Player_3"],
            "params": {"use_runner": False},
        },
    ).json()
    session_id = payload["session_id"]

    response = client.post(f"/api/sessions/{session_id}/moderator/set-controller?seat=Player_2&controller=human")
    assert response.status_code == 200
    first_link = response.json()["join_link"]
    assert first_link.startswith("http://testserver/player?token=")

    seats = client.get(f"/api/sessions/{session_id}/seats").json()
    by_id = {seat["seat_id"]: seat for seat in seats}
    assert by_id["Player_2"]["controller_type"] == "human"
    assert by_id["Player_2"]["join_link"] == first_link

    reset = client.post(f"/api/sessions/{session_id}/moderator/reset-link?seat=Player_2")
    assert reset.status_code == 200
    second_link = reset.json()["join_link"]
    assert second_link.startswith("http://testserver/player?token=")
    assert second_link != first_link

    human_count = client.post(f"/api/sessions/{session_id}/moderator/set-human-count?count=2")
    assert human_count.status_code == 200
    seats = client.get(f"/api/sessions/{session_id}/seats").json()
    assert [seat["controller_type"] for seat in seats] == ["human", "human", "ai"]

    takeover = client.post(f"/api/sessions/{session_id}/moderator/takeover?seat=Player_1")
    assert takeover.status_code == 200
    seats = client.get(f"/api/sessions/{session_id}/seats").json()
    by_id = {seat["seat_id"]: seat for seat in seats}
    assert by_id["Player_1"]["controller_type"] == "ai"
    assert by_id["Player_1"]["join_link"] == ""


@pytest.mark.asyncio
async def test_moderator_submit_api_clears_pending_action() -> None:
    """主持人代操作应提交并清除当前 pending action。"""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="moderator_submit_test",
        script_path="scripts/moderator-submit.yaml",
        seat_ids=["Player_1"],
        human_seat_ids={"Player_1"},
        params={"use_runner": False},
    )
    request = runtime.action_service.create_request("Player_1", "请选择", kind="vote", candidates=["A", "B"])

    app = create_app(registry=registry, catalog=GameCatalog(scripts_root="missing"))
    client = TestClient(app)
    response = client.post(
        f"/api/sessions/{runtime.session.session_id}/moderator/submit?seat=Player_1",
        json={"data": {"vote": "A"}, "text": "A"},
    )

    assert response.status_code == 200
    assert response.json()["request_id"] == request.request_id
    assert runtime.action_service.get_current_request("Player_1") is None


def test_host_frontend_uses_supported_moderator_routes() -> None:
    """Host 前端不能再调用 unsupported moderator 占位接口。"""
    js_path = "drama_engine/service/frontend/live_viewer.js"
    text = __import__("pathlib").Path(js_path).read_text(encoding="utf-8")
    assert "unsupported" not in text
    assert "/moderator/submit" in text
    assert "/moderator/set-controller" in text
    assert "/moderator/set-human-count" in text


def test_host_frontend_keeps_role_whispers_as_speech_bubbles() -> None:
    """Host 前端不能因“查验 / 守护”等词隐藏角色低声发言气泡。"""
    js_path = "drama_engine/service/frontend/live_viewer.js"
    text = __import__("pathlib").Path(js_path).read_text(encoding="utf-8")

    assert "function shouldHideMechanicalActionBubble" in text
    assert "blockedKeywords.some((keyword) => value.includes(keyword))" not in text
    assert "mechanicalPrefixes.some((prefix) => value.startsWith(prefix))" not in text
    assert "value.startsWith('{') && value.includes('\"action\"')" in text
    assert "formatObjectValue" in text
    assert "formatStringValue" in text
    assert "JSON.parse(text)" in text
    assert "我选择${choice}" in text
    assert "JSON.stringify(value)" not in text
    assert "该玩家" in text
    assert "玩家第一人称" in text
    assert "event.sender !== event.actor" not in text
    assert "isPlayerActorName(event.sender)" in text
    assert "sender == actor" in text


def test_host_frontend_queues_bubbles_instead_of_overwriting() -> None:
    """Host 前端应按队列展示连续发言，避免后一条气泡直接覆盖前一条。"""
    js_path = "drama_engine/service/frontend/live_viewer.js"
    text = __import__("pathlib").Path(js_path).read_text(encoding="utf-8")

    assert "bubbleQueue" in text
    assert "bubbleActive" in text
    assert "function activateNextBubble" in text
    assert "player.bubbleQueue.push" in text
    assert "activateNextBubble(actor);" in text
    assert "seenSpeechKeys: new Map()" in text
    assert "duplicateWindow" in text
    assert "initialReplaySeqMax" in text
    assert "latestSpeechText" not in text
    assert "latestSpeechSeq" not in text
    assert "falling back to historical speech" in text
    assert "bubbleQueueGapMs" in text
    assert "renderRoundTable();" in text


def test_host_frontend_excludes_moderator_from_players() -> None:
    """Host 前端不应把主持人/系统事件渲染成 9999 号玩家。"""
    js_path = "drama_engine/service/frontend/live_viewer.js"
    text = __import__("pathlib").Path(js_path).read_text(encoding="utf-8")

    assert "if (!isPlayerActorName(actor)) return null" in text
    assert "state.playerOrder.filter(isPlayerActorName)" in text
    assert "fake seats" in text


def test_host_frontend_updates_speaker_from_player_perceive_events() -> None:
    """Host 右侧当前发言卡应跟随玩家自听/私聊事件，而不只依赖 act 事件。"""
    js_path = "drama_engine/service/frontend/live_viewer.js"
    text = __import__("pathlib").Path(js_path).read_text(encoding="utf-8")

    perceive_block = text.split('if (event.type === "perceive")', 1)[1].split('if (event.type === "act")', 1)[0]
    assert "isPlayerActorName(event.sender)" in perceive_block
    assert "showBubble(event.sender" in perceive_block
    assert "shouldShowSpeechBubble(event.text)" in perceive_block
    assert "updateSpeaker(event.sender, displayValue(event.text))" in perceive_block


def test_host_dashboard_removes_pending_moderator_panel() -> None:
    """Host Dashboard 不再显示待处理动作/主持人操作面板。"""
    host_path = "drama_engine/service/frontend/host.html"
    js_path = "drama_engine/service/frontend/live_viewer.js"
    host = __import__("pathlib").Path(host_path).read_text(encoding="utf-8")
    text = __import__("pathlib").Path(js_path).read_text(encoding="utf-8")

    assert "pendingPanel" not in host
    assert "待处理动作 / 主持人操作" not in host
    assert "pendingList" not in host
    assert "seatsList" not in host
    assert 'const pendingPanel = el("pendingPanel")' in text
    assert 'if (el("pendingPanel"))' in text


def test_step_gate_api_controls_session_gate() -> None:
    """Web API 应能开启 step mode、查询状态并单步放行。"""
    app = create_app(registry=SessionRegistry(), catalog=GameCatalog(scripts_root="missing"))
    client = TestClient(app)
    payload = client.post(
        "/api/sessions",
        json={
            "game_id": "step_api_game",
            "script_path": "scripts/step-api.yaml",
            "seat_ids": ["Player_1"],
            "params": {"use_runner": False},
        },
    ).json()
    session_id = payload["session_id"]

    enabled = client.post(f"/api/sessions/{session_id}/step-mode?enabled=true")
    assert enabled.status_code == 200
    assert enabled.json()["gate"]["step_mode"] is True

    status = client.get(f"/api/sessions/{session_id}/step-gate")
    assert status.status_code == 200
    assert status.json()["step_mode"] is True

    step = client.post(f"/api/sessions/{session_id}/step?count=2")
    assert step.status_code == 200
    assert step.json()["gate"]["permits"] == 2
