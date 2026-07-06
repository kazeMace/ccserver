"""Tests for Drama Engine Web multi-session core boundaries."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from drama_engine.core.engine import ActionRequest, ServiceHumanInputPort
from drama_engine.core.session.events import SessionEventStore
from drama_engine.core.session.persistence import JsonSessionStore
from drama_engine.core.session.runtime import GameRuntime
from drama_engine.core.session.registry import SessionRegistry
from drama_engine.core.runner.base import BasicGameRunner, build_runner_context
from drama_engine.core.ports.actions import RuntimeActionPort, RuntimeActionServiceRouter
from drama_engine.core.ports.memory import InMemoryRuntimeMemoryBackend
from drama_engine.core.runtime_spec import RuntimeSpec
from drama_engine.core.session.actions import ActionRequestService, ActionRequestStore


@pytest.mark.asyncio
async def test_registry_can_create_two_isolated_sessions() -> None:
    """两个 session 必须拥有独立 runtime/service/store。"""
    registry = SessionRegistry()

    first = await registry.create_session(
        game_id="werewolf",
        script_path="scripts/fixed_flow/deduction/werewolf_v1_guard.yaml",
        seat_ids=["Player_1", "Player_2"],
        human_seat_ids={"Player_1"},
        params={"use_runner": False},
    )
    second = await registry.create_session(
        game_id="dating_show",
        script_path="scripts/korean_dating_show.yaml",
        seat_ids=["Player_1", "Player_2"],
        human_seat_ids={"Player_1"},
        params={"use_runner": False},
    )

    assert first.session.session_id != second.session.session_id
    assert first.action_service is not second.action_service
    assert first.event_store is not second.event_store
    assert first.session is not second.session


@pytest.mark.asyncio
async def test_runtime_service_ports_expose_registry_resources(tmp_path) -> None:
    """Runtime service ports 应暴露 registry 持有的 token service 和持久化 store。"""
    store = JsonSessionStore(tmp_path / "store")
    registry = SessionRegistry(store=store)

    runtime = await registry.create_session(
        game_id="ports",
        script_path="scripts/ports.yaml",
        seat_ids=["Player_1"],
        params={"use_runner": False},
    )

    assert runtime.service.token_service is registry.token_service
    assert runtime.service.persistence is store


@pytest.mark.asyncio
async def test_same_seat_name_in_two_sessions_has_isolated_pending_actions() -> None:
    """两局都有 Player_1 时，pending action 不能互相覆盖。"""
    registry = SessionRegistry()
    first = await registry.create_session(
        game_id="werewolf",
        script_path="scripts/fixed_flow/deduction/werewolf_v1_guard.yaml",
        seat_ids=["Player_1"],
        human_seat_ids={"Player_1"},
        params={"use_runner": False},
    )
    second = await registry.create_session(
        game_id="werewolf",
        script_path="scripts/fixed_flow/deduction/werewolf_v1_guard.yaml",
        seat_ids=["Player_1"],
        human_seat_ids={"Player_1"},
        params={"use_runner": False},
    )

    first_request = first.action_service.create_request("Player_1", "first cue")
    second_request = second.action_service.create_request("Player_1", "second cue")

    assert first_request.request_id != second_request.request_id
    assert first.action_service.get_current_request("Player_1") == first_request
    assert second.action_service.get_current_request("Player_1") == second_request

    first_submission = await first.action_service.submit(
        seat_id="Player_1",
        source="human",
        data={"value": "A"},
        text="A",
    )

    assert first_submission is not None
    assert first.action_service.get_current_request("Player_1") is None
    assert second.action_service.get_current_request("Player_1") == second_request


def test_service_action_service_uses_shared_store_type() -> None:
    """Service action service should use the shared storage abstraction."""
    service_action = ActionRequestService("session-a")

    assert isinstance(service_action._store, ActionRequestStore)
    assert service_action._requests is service_action._store.requests


@pytest.mark.asyncio
async def test_service_action_facade_validates_candidates_like_runtime_actions() -> None:
    """Service action facade should keep pending request after invalid candidates."""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="service-action-validation",
        script_path="scripts/service-action-validation.yaml",
        seat_ids=["Player_1"],
        human_seat_ids={"Player_1"},
        params={"use_runner": False},
    )
    request = runtime.action_service.create_request(
        "Player_1",
        "请选择",
        kind="vote",
        candidates=["A", "B"],
        metadata={"scene_display_name": "投票"},
    )

    failed = await runtime.action_service.submit(
        seat_id="Player_1",
        source="human",
        data={"vote": "C"},
        text="C",
    )

    assert failed is not None
    assert failed.validated is False
    assert "不在候选集中" in failed.validation_error
    assert runtime.action_service.get_current_request("Player_1") == request

    accepted = await runtime.action_service.submit(
        seat_id="Player_1",
        source="human",
        data={"vote": "A"},
        text="A",
    )

    assert accepted.validated is True
    assert accepted.submission_id
    assert accepted.text == "【Player_1｜投票】A"
    assert runtime.action_service.get_current_request("Player_1") is None


@pytest.mark.asyncio
async def test_service_action_facade_validates_collect_model() -> None:
    """Service action facade should validate data with collect_model."""

    class VoteModel(BaseModel):
        """Small Pydantic model used by action validation tests."""

        vote: str
        reason: str

    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="service-action-schema-validation",
        script_path="scripts/service-action-schema-validation.yaml",
        seat_ids=["Player_1"],
        human_seat_ids={"Player_1"},
        params={"use_runner": False},
    )
    request = runtime.action_service.create_request(
        "Player_1",
        "请选择",
        kind="vote",
        candidates=["A", "B"],
        metadata={"collect_model": VoteModel},
    )

    failed = await runtime.action_service.submit(
        seat_id="Player_1",
        source="human",
        data={"vote": "A"},
        text="A",
    )

    assert failed.validated is False
    assert "schema 校验失败" in failed.validation_error
    assert runtime.action_service.get_current_request("Player_1") == request

    accepted = await runtime.action_service.submit(
        seat_id="Player_1",
        source="human",
        data={"vote": "B", "reason": "测试"},
        text="B",
    )

    assert accepted.validated is True
    assert accepted.data == {"vote": "B", "reason": "测试"}
    assert runtime.action_service.get_current_request("Player_1") is None


@pytest.mark.asyncio
async def test_service_action_facade_applies_timeout_policy_from_session_params() -> None:
    """Service action port should apply shared timeout policy."""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="service-action-timeout",
        script_path="scripts/service-action-timeout.yaml",
        seat_ids=["Player_1"],
        human_seat_ids={"Player_1"},
        params={
            "use_runner": False,
            "timeout_policy": {
                "default_seconds": 0.01,
                "structured": "abstain",
            },
        },
    )
    request = runtime.action_service.create_request(
        "Player_1",
        "请选择",
        kind="generic",
        candidates=["A", "B"],
    )

    submission = await asyncio.wait_for(
        runtime.action_service.service_action.wait_submission(request.request_id),
        timeout=2,
    )
    await runtime.action_service.service_action.stop()

    assert submission.source == "timeout_default"
    assert submission.data == {"action": False, "target": None}
    assert submission.text == "弃权（超时）"
    assert runtime.action_service.get_current_request("Player_1") is None


@pytest.mark.asyncio
async def test_service_action_facade_cancel_all_stops_timeout_watcher() -> None:
    """cancel_all 应同时停止 service action facade 的 deadline watcher。"""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="service-action-cancel-timeout",
        script_path="scripts/service-action-cancel-timeout.yaml",
        seat_ids=["Player_1"],
        human_seat_ids={"Player_1"},
        params={
            "use_runner": False,
            "timeout_policy": {"default_seconds": 10},
        },
    )

    runtime.action_service.create_request("Player_1", "请选择", kind="generic")
    assert runtime.action_service.service_action.is_running is True

    runtime.action_service.cancel_all()

    assert runtime.action_service.service_action.is_running is False


@pytest.mark.asyncio
async def test_player_tokens_resolve_session_and_seat() -> None:
    """同名 seat 在不同 session 中应该生成不同 token。"""
    registry = SessionRegistry()
    first = await registry.create_session(
        game_id="werewolf",
        script_path="scripts/fixed_flow/deduction/werewolf_v1_guard.yaml",
        seat_ids=["Player_1"],
        human_seat_ids={"Player_1"},
        params={"use_runner": False},
    )
    second = await registry.create_session(
        game_id="werewolf",
        script_path="scripts/fixed_flow/deduction/werewolf_v1_guard.yaml",
        seat_ids=["Player_1"],
        human_seat_ids={"Player_1"},
        params={"use_runner": False},
    )

    first_token = registry.token_service.token_for_seat(first.session.session_id, "Player_1")
    second_token = registry.token_service.token_for_seat(second.session.session_id, "Player_1")

    assert first_token
    assert second_token
    assert first_token != second_token

    first_claim = registry.token_service.validate(first_token)
    second_claim = registry.token_service.validate(second_token)

    assert first_claim is not None
    assert second_claim is not None
    assert first_claim.session_id == first.session.session_id
    assert second_claim.session_id == second.session.session_id
    assert first_claim.seat_id == "Player_1"
    assert second_claim.seat_id == "Player_1"


@pytest.mark.asyncio
async def test_registry_uses_service_controls_for_seat_links() -> None:
    """SessionRegistry 应由 service 控制器处理 seat token 和 join link。"""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="werewolf",
        script_path="scripts/fixed_flow/deduction/werewolf_v1_guard.yaml",
        seat_ids=["Player_1", "Player_2"],
        params={"use_runner": False},
    )

    link = await registry.set_seat_controller(
        runtime.session.session_id,
        "Player_1",
        "human",
    )

    assert link.startswith("/player?token=")
    assert runtime.player_links["Player_1"] == link
    assert "Player_1" in runtime.session.human_seat_ids
    assert registry.token_service.token_for_seat(runtime.session.session_id, "Player_1")
    assert runtime.event_store.host_backlog()[-1]["kind"] == "seat_controller_changed"

    reset_link = await registry.reset_join_link(runtime.session.session_id, "Player_1")

    assert reset_link.startswith("/player?token=")
    assert reset_link != link
    assert runtime.player_links["Player_1"] == reset_link
    assert runtime.event_store.host_backlog()[-1]["kind"] == "seat_link_reset"

    ai_link = await registry.set_seat_controller(
        runtime.session.session_id,
        "Player_1",
        "ai",
    )

    assert ai_link == ""
    assert "Player_1" not in runtime.player_links
    assert "Player_1" not in runtime.session.human_seat_ids


def test_event_store_backlog_is_per_session() -> None:
    """事件回放必须按 session 隔离。"""
    first = SessionEventStore("session-a")
    second = SessionEventStore("session-b")

    first.append_public({"kind": "message", "text": "A"})
    second.append_public({"kind": "message", "text": "B"})
    first.append_private("Player_1", {"kind": "secret", "text": "private A"})
    second.append_private("Player_1", {"kind": "secret", "text": "private B"})

    first_public = first.public_backlog()[0]
    second_public = second.public_backlog()[0]
    first_private = first.private_backlog("Player_1")[0]
    second_private = second.private_backlog("Player_1")[0]

    assert first_public["kind"] == "message"
    assert first_public["text"] == "A"
    assert first_public["session_id"] == "session-a"
    assert first_public["audience"] == "public"
    assert first_public["seq"] == 1

    assert second_public["kind"] == "message"
    assert second_public["text"] == "B"
    assert second_public["session_id"] == "session-b"
    assert second_public["audience"] == "public"
    assert second_public["seq"] == 1

    assert first_private["kind"] == "secret"
    assert first_private["text"] == "private A"
    assert first_private["session_id"] == "session-a"
    assert first_private["audience"] == "private"
    assert first_private["seat_id"] == "Player_1"

    assert second_private["kind"] == "secret"
    assert second_private["text"] == "private B"
    assert second_private["session_id"] == "session-b"
    assert second_private["audience"] == "private"
    assert second_private["seat_id"] == "Player_1"


def test_party_session_runtime_is_target_session_container() -> None:
    """GameRuntime should be the target architecture session container."""
    assert GameRuntime.__name__ == "GameRuntime"


@pytest.mark.asyncio
async def test_party_session_runtime_uses_summary_provider_for_runner_state() -> None:
    """Runtime summary should include BasicGameRunner status via SummaryProvider."""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="werewolf",
        script_path="drama_engine/scripts/interactive_session/deduction/werewolf.yaml",
        seat_ids=["Player_1", "Player_2"],
        params={"use_runner": True, "dry_run": True},
    )

    summary = runtime.summary()
    host_summary = runtime.host_summary()

    assert runtime.summary_provider is not None
    assert summary["runner"]["runtime_type"] == "interactive_session"
    assert summary["runtime_state"]["phase"] == "idle"
    assert summary["runtime_state"]["metadata"]["runner"] == "InteractiveSessionRunner"
    assert host_summary["audience"] == "host"
    assert host_summary["runner_summary"]["runner"] == "InteractiveSessionRunner"


@pytest.mark.asyncio
async def test_interactive_runner_uses_runtime_action_router_for_human_actors() -> None:
    """interactive_session human actors should receive the runtime action router."""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="werewolf",
        script_path="drama_engine/scripts/interactive_session/deduction/werewolf.yaml",
        seat_ids=["Player_1", "Player_2"],
        human_seat_ids={"Player_1"},
        params={"use_runner": True, "dry_run": True},
    )
    await runtime.assign()
    actor = runtime.actor_runtime.cast.get("Player_1")

    assert runtime.runner.__class__.__name__ == "InteractiveSessionRunner"
    assert actor._controller._input_port._service is runtime.action_service


@pytest.mark.asyncio
async def test_party_session_runtime_provides_memory_store_to_runner_context() -> None:
    """GameRuntime should expose one shared RuntimeMemoryStore."""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="memory",
        script_path="scripts/memory.yaml",
        seat_ids=["Player_1"],
        params={"use_runner": False},
    )

    context = build_runner_context(
        runtime=runtime,
        declaration=RuntimeSpec(type="game_session"),
    )
    context.memory_store.append("transcript", {"text": "hello"})

    assert runtime.memory_store.latest("transcript") == {"text": "hello"}
    assert runtime.memory_store.snapshot() == {"transcript": [{"text": "hello"}]}

    await runtime.restart()

    assert runtime.memory_store.snapshot() == {}


@pytest.mark.asyncio
async def test_runtime_memory_store_can_use_long_term_backend() -> None:
    """RuntimeMemoryStore 应区分 session memory 和跨局长期记忆 backend。"""
    backend = InMemoryRuntimeMemoryBackend()
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="memory-backend",
        script_path="scripts/memory.yaml",
        seat_ids=["Player_1"],
        params={"use_runner": False},
    )
    runtime.memory_store.bind_backend(backend)

    runtime.memory_store.append("session.bucket", {"text": "only this session"})
    runtime.memory_store.remember_long_term("group_chat:topic", {"text": "old discussion"})

    assert runtime.memory_store.latest("session.bucket") == {"text": "only this session"}
    assert runtime.memory_store.recall_long_term("group_chat:topic") == [{"text": "old discussion"}]


@pytest.mark.asyncio
async def test_party_session_runtime_registers_runner_with_metadata() -> None:
    """Runtime should expose register_runner as the runner mount API."""

    class MinimalRunner(BasicGameRunner):
        """Small concrete runner for runtime registration tests."""

        async def assign(self) -> None:
            self.runtime.session.set_status("assigned")

        async def start(self) -> None:
            self.runtime.session.set_status("running")

    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="manual-runner",
        script_path="scripts/manual-runner.yaml",
        seat_ids=["Player_1"],
        params={"use_runner": False},
    )
    runner = MinimalRunner(runtime=runtime, dry_run=True)

    result = runtime.register_runner(runner)

    assert result is runner
    assert runtime.runner is runner
    assert runtime.summary()["runtime_state"]["metadata"]["runner"] == "MinimalRunner"


@pytest.mark.asyncio
async def test_party_session_runtime_events_api_exposes_backlog_and_subscribers() -> None:
    """Runtime.events should expose event backlog and event subscribers."""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="events",
        script_path="scripts/events.yaml",
        seat_ids=["Player_1"],
        params={"use_runner": False},
    )

    runtime.event_store.append_public({"kind": "public_note"})
    runtime.event_store.append_host({"kind": "host_note"})
    runtime.event_store.append_private("Player_1", {"kind": "private_note"})

    assert [event["kind"] for event in runtime.events("public")] == ["public_note"]
    assert [event["kind"] for event in runtime.events("host")] == ["public_note", "host_note"]
    assert [event["kind"] for event in runtime.events("private", seat_id="Player_1")] == ["private_note"]

    subscriber = runtime.events("private", seat_id="Player_1", subscribe=True)
    assert subscriber.audience == "private"
    assert subscriber.seat_id == "Player_1"
    assert subscriber.queue.get_nowait()["kind"] == "private_note"


@pytest.mark.asyncio
async def test_party_session_runtime_updates_runtime_phase() -> None:
    """Runtime control APIs should keep RuntimeState.phase current."""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="phase",
        script_path="scripts/phase.yaml",
        seat_ids=["Player_1"],
        params={"use_runner": False},
    )

    assert runtime.runtime_state.phase == "idle"
    await runtime.assign()
    assert runtime.runtime_state.phase == "assigned"
    await runtime.start()
    assert runtime.runtime_state.phase == "running"
    await runtime.pause()
    assert runtime.runtime_state.phase == "paused"
    await runtime.resume()
    assert runtime.runtime_state.phase == "running"
    before_step = runtime.runtime_state.phase
    await runtime.step_gate.set_step_mode(True)
    await runtime.step()
    assert runtime.runtime_state.phase == before_step
    await runtime.terminate()
    assert runtime.runtime_state.phase == "terminated"


@pytest.mark.asyncio
async def test_party_session_runtime_emits_lifecycle_hooks() -> None:
    """Runtime lifecycle hooks should wrap lifecycle control actions."""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="hooked",
        script_path="scripts/hooked.yaml",
        seat_ids=["Player_1"],
        params={"use_runner": False},
    )
    calls = []

    def record(runtime, action, payload):
        calls.append((runtime.session.session_id, action, dict(payload)))

    runtime.lifecycle_hooks.register("before_assign", record)
    runtime.lifecycle_hooks.register("after_assign", record)
    runtime.lifecycle_hooks.register("before_step", record)
    runtime.lifecycle_hooks.register("after_step", record)

    await runtime.assign()
    await runtime.step_gate.set_step_mode(True)
    await runtime.step(count=2)

    assert calls == [
        (runtime.session.session_id, "assign", {}),
        (runtime.session.session_id, "assign", {}),
        (runtime.session.session_id, "step", {"count": 2}),
        (runtime.session.session_id, "step", {"count": 2}),
    ]


@pytest.mark.asyncio
async def test_party_session_runtime_action_view_wraps_service_action_facade() -> None:
    """Runtime action_view should expose service pending actions."""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="actions",
        script_path="scripts/actions.yaml",
        seat_ids=["Player_1"],
        human_seat_ids={"Player_1"},
        params={"use_runner": False},
    )

    request = runtime.action_service.create_request("Player_1", "请选择", kind="vote", candidates=["A"])
    router_current = runtime.action_service.current_action("Player_1")
    current = runtime.action_view.current_action(runtime, "Player_1")
    pending = runtime.action_view.pending_summary(runtime)

    assert isinstance(runtime.action_service, RuntimeActionServiceRouter)
    assert isinstance(runtime.runner, BasicGameRunner) or runtime.runner is None
    assert runtime.service.action_view is runtime.action_service
    assert router_current["request_id"] == request.request_id
    assert runtime.action_service.pending_summary()[0]["request_id"] == request.request_id
    assert current["request_id"] == request.request_id
    assert current["candidates"] == ["A"]
    assert pending[0]["request_id"] == request.request_id


@pytest.mark.asyncio
async def test_runtime_action_router_supports_shared_human_actor_input() -> None:
    """Runtime action router should support human actors shared by all runners."""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="human-router",
        script_path="scripts/actions.yaml",
        seat_ids=["Player_1"],
        human_seat_ids={"Player_1"},
        params={"use_runner": False},
    )
    cast = runtime.actor_runtime.create_cast(
        player_names=["Player_1"],
        human_seat_ids={"Player_1"},
        action_service=runtime.service.action_view,
        dry_run=True,
    )
    actor = cast.get("Player_1")

    task = asyncio.create_task(actor.act("请发言"))
    for _ in range(20):
        request = runtime.action_service.get_current_request("Player_1")
        if request is not None:
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("human actor 没有创建 pending request")

    assert runtime.action_service.current_action("Player_1")["cue"] == "请发言"
    await runtime.action_service.submit(
        seat_id="Player_1",
        source="human",
        data={"text": "收到"},
        text="收到",
    )
    response = await asyncio.wait_for(task, timeout=1)

    assert response["actor"] == "Player_1"
    assert response["text"] == "收到"


@pytest.mark.asyncio
async def test_runtime_action_view_uses_runner_action_port() -> None:
    """RuntimeActionView 应使用 runner 显式 action_port，而不是探测私有字段。"""

    class FakeActionPort:
        """Test action port with the same protocol as runtime ports."""

        def __init__(self) -> None:
            self.submitted = None

        def pending_summary(self):
            """Return a deterministic pending summary."""
            return [{"seat_id": "Player_1", "request_id": "runner-request"}]

        def current_action(self, seat_id):
            """Return a deterministic current action."""
            return {
                "seat_id": seat_id,
                "request_id": "runner-request",
                "kind": "vote",
                "cue": "runner cue",
            }

        def current_request_object(self, seat_id):
            """Return a deterministic raw request object."""
            return {"seat_id": seat_id, "request_id": "runner-request", "cue": "runner cue"}

        async def submit_current(self, runtime, seat_id, source, data, text):
            """Record submit parameters and return a deterministic result."""
            self.submitted = (runtime.session.session_id, seat_id, source, data, text)
            return {"request_id": "runner-request"}

        def cancel_all(self):
            """No-op cancel hook required by RuntimeActionPort."""
            return None

    class FakeRunner:
        """Runner exposing only action_port."""

        def __init__(self, port) -> None:
            self._port = port

        def action_port(self):
            """Return the explicit action port."""
            return self._port

    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="actions",
        script_path="scripts/actions.yaml",
        seat_ids=["Player_1"],
        human_seat_ids={"Player_1"},
        params={"use_runner": False},
    )
    port = FakeActionPort()
    runtime.runner = FakeRunner(port)

    assert isinstance(port, RuntimeActionPort)
    assert runtime.action_view.current_action(runtime, "Player_1")["cue"] == "runner cue"
    assert runtime.action_view.pending_summary(runtime)[0]["request_id"] == "runner-request"
    assert runtime.action_service.get_current_request("Player_1")["request_id"] == "runner-request"

    result = await runtime.action_view.submit_current(
        runtime,
        seat_id="Player_1",
        source="human",
        data={"vote": "A"},
        text="A",
    )

    assert result == {"request_id": "runner-request"}
    assert port.submitted == (
        runtime.session.session_id,
        "Player_1",
        "human",
        {"vote": "A"},
        "A",
    )


@pytest.mark.asyncio
async def test_runtime_action_router_supports_service_human_input_port() -> None:
    """ServiceHumanInputPort should work with the runtime action router boundary."""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="group-chat-human",
        script_path="scripts/group-chat-human.yaml",
        seat_ids=["Player_1"],
        human_seat_ids={"Player_1"},
        params={"use_runner": False},
    )
    port = ServiceHumanInputPort(
        service=runtime.action_service,
        seat_id="Player_1",
    )
    request = ActionRequest(
        request_id="local-request",
        seat_id="Player_1",
        cue="请发言",
        kind="speech",
    )

    task = asyncio.create_task(port.request_action(request))
    pending = None
    for _ in range(20):
        pending = runtime.action_service.get_current_request("Player_1")
        if pending is not None:
            break
        await asyncio.sleep(0.01)

    assert pending is not None
    assert pending.cue == "请发言"
    submission = await runtime.action_service.submit(
        seat_id="Player_1",
        source="human",
        data={"text": "收到"},
        text="收到",
    )
    result = await task

    assert submission is not None
    assert result.request_id == pending.request_id
    assert result.data["text"] == "收到"
    assert result.text.endswith("收到")
