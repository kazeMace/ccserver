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
                "game_id": "who_is_undercover",
                "script_path": "drama_engine/scripts/interactive_session/deduction/who_is_undercover.yaml",
                "seat_ids": [f"Player_{index}" for index in range(1, 7)],
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


def test_create_page_can_create_guard_preset_room_and_return_player_links() -> None:
    """首页创建页使用当前唯一 preset 时，应返回真人玩家链接。"""
    app = create_app(registry=SessionRegistry(), catalog=GameCatalog(scripts_root="missing"))
    client = TestClient(app)

    response = client.post(
        "/api/sessions",
        json={
            "game_id": "werewolf_v1_12p_guard",
            "script_path": "drama_engine/scripts/interactive_session/deduction/werewolf_12p_guard.yaml",
            "seat_ids": [f"Player_{index}" for index in range(1, 13)],
            "human_seat_ids": ["Player_1", "Player_2", "Player_3"],
            "params": {"total_players": 12, "werewolf_count": 4, "dry_run": False, "use_runner": True},
            "metadata": {
                "preset_path": "drama_engine/scripts/presets/deduction/werewolf/werewolf_v1_12p_guard.preset.yaml",
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


def test_view_snapshot_apis() -> None:
    """player/public/host view API 应该可用。"""
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


@pytest.mark.asyncio
async def test_player_input_api_submits_to_runner_action_service() -> None:
    """真人玩家提交应路由到当前 ActionRequestService。"""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="human_input_test",
        script_path="drama_engine/scripts/interactive_session/deduction/werewolf_12p_guard.yaml",
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


def test_checkpoint_and_rollback_endpoints() -> None:
    """checkpoint / rollback-points / rollback API 应经 GameInstance 工作。"""
    app = create_app(registry=SessionRegistry(), catalog=GameCatalog(scripts_root="missing"))
    with TestClient(app) as client:
        created = client.post(
            "/api/sessions",
            json={
                "game_id": "who_is_undercover",
                "script_path": "drama_engine/scripts/interactive_session/deduction/who_is_undercover.yaml",
                "seat_ids": [f"Player_{index}" for index in range(1, 7)],
                "params": {"dry_run": True, "use_runner": True},
            },
        )
        session_id = created.json()["session_id"]
        assert client.post(f"/api/sessions/{session_id}/assign").status_code == 200

        # 建 checkpoint
        ckpt = client.post(f"/api/sessions/{session_id}/checkpoint?reason=before_start")
        assert ckpt.status_code == 200
        checkpoint_id = ckpt.json()["checkpoint"]["checkpoint_id"]

        # 列出 rollback points
        points = client.get(f"/api/sessions/{session_id}/rollback-points")
        assert points.status_code == 200
        assert any(p["checkpoint_id"] == checkpoint_id for p in points.json())

        # 回滚
        rolled = client.post(f"/api/sessions/{session_id}/rollback?checkpoint_id={checkpoint_id}")
        assert rolled.status_code == 200
        assert rolled.json()["ok"] is True
