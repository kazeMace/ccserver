"""Tests for real YAML/Director runner integration."""

from __future__ import annotations

import asyncio

import pytest

from drama_engine.core.session.registry import SessionRegistry


@pytest.mark.asyncio
async def test_runner_can_assign_real_yaml_script() -> None:
    """assign 应执行真实 YAML compiler + Director.setup 并产生角色快照。"""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="werewolf_v1_guard",
        script_path="drama_engine/core/scripts/werewolf_v1_guard.yaml",
        seat_ids=[f"Player_{index}" for index in range(1, 13)],
        human_seat_ids=set(),
        params={"dry_run": True, "use_runner": True},
    )

    await registry.assign_session(runtime.session.session_id)

    assert runtime.session.status == "assigned"
    assert runtime.actor_runtime is not None
    assert runtime.actor_runtime.cast is runtime.runner._state.cast
    assert runtime.actor_runtime.casting_service is runtime.runner._state.casting_service
    assert runtime.runner._state.casting_service is runtime.runner._state.director.casting_service
    assert runtime.runner._state.casting_service.assigned is True
    roles = [seat.role_snapshot for seat in runtime.session.seats.values()]
    assert any(role == "werewolf" for role in roles)
    assert len([role for role in roles if role]) == 12
    assert any(event["kind"] == "session_assigned" for event in runtime.event_store.public_backlog())


def test_prepare_actor_for_scene_clears_stale_human_candidates() -> None:
    """无 candidates 的上警幕必须清空上一幕候选，避免真人上警被目标校验拦截。"""
    from drama_engine.core.engine import (
        ActionSubmission,
        HumanActorController,
        SeatActor,
        Single,
        Scene,
        State,
        Vocabulary,
        _prepare_actor_for_scene,
    )
    from drama_engine.core.session.actions import _validate_candidates

    class DummyInputPort:
        async def send_profile(self, seat_id, profile):
            return None

        async def send_perception(self, seat_id, msg):
            return None

        async def request_action(self, request, collect_model=None):
            return ActionSubmission(
                submission_id="submission",
                request_id=request.request_id,
                seat_id=request.seat_id,
                source="human",
                data={"action": True},
                text="我选择上警",
                validated=True,
                validation_error="",
            )

        async def send_input_error(self, seat_id, request_id, error):
            raise AssertionError(error)

    controller = HumanActorController(DummyInputPort())
    actor = SeatActor("Player_1", controller)
    controller.set_candidates(["Player_10", "Player_11"])

    scene = Scene(
        name="sheriff-join",
        scope="town",
        participants=lambda state: {"Player_1"},
        cue="是否上警竞选警长？",
        dialogue_policy=Single(),
        candidates=None,
    )
    state = State(Vocabulary(roles=set(), factions=set(), scopes=set(), abilities=set()))
    state.register_entity("GAME", {})

    _prepare_actor_for_scene(actor, scene, state)

    assert controller._candidates == []
    assert _validate_candidates({"action": True}, controller._candidates) == ""



@pytest.mark.asyncio
async def test_runner_can_start_and_finish_real_yaml_dry_run() -> None:
    """start 应后台执行 Director.run_flow，dry-run 下最终进入 ended 或 failed。"""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="werewolf_v1_guard",
        script_path="drama_engine/core/scripts/werewolf_v1_guard.yaml",
        seat_ids=[f"Player_{index}" for index in range(1, 13)],
        human_seat_ids=set(),
        params={"dry_run": True, "use_runner": True},
    )

    await registry.assign_session(runtime.session.session_id)
    await registry.start_session(runtime.session.session_id)

    assert runtime.director_task is not None
    await asyncio.wait_for(runtime.director_task, timeout=20)

    assert runtime.session.status == "ended"
    assert runtime.runtime_state.phase == "ended"
    assert any(event["kind"] == "session_ended" for event in runtime.event_store.public_backlog())


@pytest.mark.asyncio
async def test_runner_host_backlog_contains_dashboard_trace_events() -> None:
    """Host dashboard 需要 act/narration 事件来展示发言、头像气泡和广播。"""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="werewolf_v1_guard",
        script_path="drama_engine/core/scripts/werewolf_v1_guard.yaml",
        seat_ids=[f"Player_{index}" for index in range(1, 13)],
        human_seat_ids=set(),
        params={"dry_run": True, "use_runner": True},
    )

    await registry.assign_session(runtime.session.session_id)
    await registry.start_session(runtime.session.session_id)
    assert runtime.director_task is not None
    await asyncio.wait_for(runtime.director_task, timeout=20)

    host_events = runtime.event_store.host_backlog()
    public_events = runtime.event_store.public_backlog()
    assert any(event.get("kind") == "narration" and event.get("text") for event in host_events)
    assert any(event.get("kind") == "act" and event.get("actor") and event.get("text") for event in host_events)
    assert any(
        event.get("kind") == "narration" and str(event.get("scope", "")).startswith("whisper:")
        for event in host_events
    )
    assert not any(
        event.get("kind") == "narration" and str(event.get("scope", "")).startswith("whisper:")
        for event in public_events
    )


def test_witch_poison_cue_distinguishes_current_night_from_history() -> None:
    """女巫毒药提示必须声明当前夜权威状态，避免把上一晚救人误判为本晚已救。"""
    from drama_engine.core.dsl.compiler import YamlCompiler
    from drama_engine.core.engine import State, StateWriter, SetAttr, _resolve_action_cue

    compiler = YamlCompiler()
    script = compiler.compile("drama_engine/core/scripts/werewolf_v1_guard.yaml")
    poison_scene = next(scene for scene in script.flow.scenes if scene.name == "witch-poison")

    state = State(script.vocab)
    state.register_entity("GAME", {})
    writer = StateWriter(state)
    writer.apply(SetAttr("GAME", "round", 2))
    writer.apply(SetAttr("GAME", "saved", False))

    cue = _resolve_action_cue(poison_scene, state, actor_name="Player_12")

    assert "当前是第 2 夜" in cue
    assert "当前状态=本晚未使用解药" in cue
    assert "之前某晚使用过解药" in cue
    assert "历史信息，不代表本晚已经用过解药" in cue



@pytest.mark.asyncio
async def test_runner_does_not_duplicate_human_actor_profile_in_private_timeline() -> None:
    """状态快照不应反复向真人玩家时间线写入身份信息。"""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="werewolf_v1_guard",
        script_path="drama_engine/core/scripts/werewolf_v1_guard.yaml",
        seat_ids=[f"Player_{index}" for index in range(1, 13)],
        human_seat_ids={"Player_1"},
        params={"dry_run": True, "use_runner": True},
    )

    await registry.assign_session(runtime.session.session_id)
    human_actor = runtime.actor_runtime.cast.get("Player_1")
    assert human_actor._controller._input_port._service is runtime.action_service

    before = runtime.event_store.private_backlog("Player_1")
    assert [event.get("kind") for event in before].count("actor_profile") == 1

    runtime.runner._push_roles_from_state(runtime.runner._state.state)
    runtime.runner._push_roles_from_state(runtime.runner._state.state)

    after = runtime.event_store.private_backlog("Player_1")
    assert [event.get("kind") for event in after].count("actor_profile") == 1



@pytest.mark.asyncio
async def test_runner_human_pending_action_can_be_submitted() -> None:
    """真实 runner 遇到真人 seat 时应产生 pending action，提交后流程可继续。"""
    registry = SessionRegistry()
    seat_ids = [f"Player_{index}" for index in range(1, 13)]
    runtime = await registry.create_session(
        game_id="werewolf_v1_guard",
        script_path="drama_engine/core/scripts/werewolf_v1_guard.yaml",
        seat_ids=seat_ids,
        human_seat_ids=set(seat_ids),
        params={"dry_run": True, "use_runner": True, "timeout_policy": {"default_seconds": 30}},
    )

    await registry.assign_session(runtime.session.session_id)
    await registry.start_session(runtime.session.session_id)
    assert runtime.director_task is not None

    seat_id, request = await _wait_for_any_human_request(runtime, seat_ids, timeout_seconds=10)
    assert seat_id is not None
    assert request is not None

    await _submit_request_for_test(runtime, seat_id, request, f"{seat_id} 自动测试提交")
    handled = 1 + await _drive_all_human_requests_until_done(runtime, seat_ids, timeout_seconds=20)

    assert handled >= 1
    assert runtime.session.status == "ended"


@pytest.mark.asyncio
async def test_runner_human_input_blocks_without_timeout_until_player_submits() -> None:
    """默认真人输入没有超时；玩家未提交时游戏必须停在 pending action。"""
    registry = SessionRegistry()
    seat_ids = [f"Player_{index}" for index in range(1, 13)]
    runtime = await registry.create_session(
        game_id="werewolf_v1_guard",
        script_path="drama_engine/core/scripts/werewolf_v1_guard.yaml",
        seat_ids=seat_ids,
        human_seat_ids=set(seat_ids),
        params={"dry_run": True, "use_runner": True},
    )

    await registry.assign_session(runtime.session.session_id)
    await registry.start_session(runtime.session.session_id)
    assert runtime.director_task is not None

    service = runtime.action_service
    seat_id, request = await _wait_for_any_human_request(runtime, seat_ids, timeout_seconds=6)

    assert seat_id is not None
    assert request is not None
    assert request.deadline_at is None
    assert request.timeout_seconds is None
    assert runtime.action_view.current_action(runtime, seat_id)["request_id"] == request.request_id
    assert any(item["request_id"] == request.request_id for item in runtime.action_view.pending_summary(runtime))
    assert not runtime.director_task.done()

    await asyncio.sleep(0.2)
    assert service.get_current_request(seat_id) == request
    assert not runtime.director_task.done()

    submission = await _submit_request_for_test(runtime, seat_id, request, "玩家提交后才继续")
    assert submission.request_id == request.request_id
    assert service.get_current_request(seat_id) != request

    handled = await _drive_all_human_requests_until_done(runtime, seat_ids, timeout_seconds=20)
    assert handled >= 0
    assert runtime.session.status == "ended"



@pytest.mark.asyncio
async def test_two_runner_sessions_do_not_share_events_or_pending_actions() -> None:
    """两个真实 runner session 同时运行时，事件和动作必须隔离。"""
    registry = SessionRegistry()
    seat_ids = [f"Player_{index}" for index in range(1, 13)]
    human_seat_ids = set(seat_ids)
    first = await registry.create_session(
        game_id="werewolf_v1_guard",
        script_path="drama_engine/core/scripts/werewolf_v1_guard.yaml",
        seat_ids=seat_ids,
        human_seat_ids=human_seat_ids,
        params={"dry_run": True, "use_runner": True, "timeout_policy": {"default_seconds": 30}},
    )
    second = await registry.create_session(
        game_id="werewolf_v1_guard",
        script_path="drama_engine/core/scripts/werewolf_v1_guard.yaml",
        seat_ids=seat_ids,
        human_seat_ids=human_seat_ids,
        params={"dry_run": True, "use_runner": True, "timeout_policy": {"default_seconds": 30}},
    )

    await registry.assign_session(first.session.session_id)
    await registry.assign_session(second.session.session_id)
    await registry.start_session(first.session.session_id)
    await registry.start_session(second.session.session_id)

    first_seat_id, first_request = await _wait_for_any_human_request(
        first,
        seat_ids,
        timeout_seconds=10,
    )
    second_seat_id, second_request = await _wait_for_any_human_request(
        second,
        seat_ids,
        timeout_seconds=10,
    )

    assert first_seat_id
    assert second_seat_id
    assert first_request is not None
    assert second_request is not None
    assert first_request.request_id != second_request.request_id
    assert first.event_store is not second.event_store
    assert first.action_service is not second.action_service
    assert first.event_store.private_backlog(first_seat_id) != []
    assert second.event_store.private_backlog(second_seat_id) != []

    await _submit_request_for_test(first, first_seat_id, first_request, "第一局发言")
    assert first.action_service.get_current_request(first_seat_id) is None
    assert second.action_service.get_current_request(second_seat_id) == second_request

    await _submit_request_for_test(second, second_seat_id, second_request, "第二局发言")

    first_done, second_done = await asyncio.gather(
        _drive_all_human_requests_until_done(first, seat_ids, timeout_seconds=20),
        _drive_all_human_requests_until_done(second, seat_ids, timeout_seconds=20),
    )
    assert first_done >= 0
    assert second_done >= 0
    assert first.session.status == "ended"
    assert second.session.status == "ended"


async def _wait_for_any_human_request(runtime, seat_ids: list[str], timeout_seconds: float):
    """等待任一真人 seat 出现 pending action。"""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    service = runtime.action_service
    while asyncio.get_running_loop().time() <= deadline:
        for seat_id in seat_ids:
            request = service.get_current_request(seat_id)
            if request is not None:
                return seat_id, request
        await asyncio.sleep(0.05)
    return None, None


async def _drive_all_human_requests_until_done(runtime, seat_ids: list[str], timeout_seconds: float) -> int:
    """测试辅助：持续提交所有真人 seat 的 pending action，直到游戏结束。"""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    handled = 0
    while runtime.director_task is not None and not runtime.director_task.done():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("等待真人流程结束超时")
        service = runtime.action_service
        for seat_id in seat_ids:
            request = service.get_current_request(seat_id)
            if request is not None:
                await _submit_request_for_test(runtime, seat_id, request, f"{seat_id} 自动测试提交")
                handled += 1
        await asyncio.sleep(0.05)
    if runtime.director_task is not None:
        await runtime.director_task
    return handled

async def _drive_human_requests_until_done(runtime, seat_id: str, timeout_seconds: float) -> int:
    """测试辅助：持续提交指定真人 seat 的 pending action，直到游戏结束。"""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    handled = 0
    while runtime.director_task is not None and not runtime.director_task.done():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("等待真人流程结束超时")
        service = runtime.action_service
        request = service.get_current_request(seat_id)
        if request is not None:
            await _submit_request_for_test(runtime, seat_id, request, f"{seat_id} 自动测试提交")
            handled += 1
        await asyncio.sleep(0.05)
    if runtime.director_task is not None:
        await runtime.director_task
    return handled


async def _submit_request_for_test(runtime, seat_id: str, request, text: str):
    """根据 request.kind/candidates 生成一个能通过 DSL collect 的测试提交。"""
    candidates = list(getattr(request, "candidates", None) or [])
    kind = getattr(request, "kind", "speech")
    if kind == "speech":
        data = {"text": text}
    elif kind == "vote":
        target = candidates[0] if candidates else "ABSTAIN"
        data = {"vote": target, "reason": text}
    elif kind == "night_action":
        target = candidates[0] if candidates else None
        data = {"action": bool(target), "target": target, "vote": target, "reason": text}
    else:
        data = {"action": False, "target": None, "text": text, "reason": text}
    assert runtime.action_service.get_current_request(seat_id) == request
    submission = await runtime.action_service.submit(
        seat_id=seat_id,
        source="human",
        data=data,
        text=text,
    )
    assert submission.validated is True, submission.validation_error
    return submission


@pytest.mark.asyncio
async def test_runner_step_mode_blocks_until_step_api_releases() -> None:
    """真实 runner 接入 step gate 后，step mode 下不会自动跑完，step 后才推进。"""
    registry = SessionRegistry()
    runtime = await registry.create_session(
        game_id="werewolf_v1_guard",
        script_path="drama_engine/core/scripts/werewolf_v1_guard.yaml",
        seat_ids=[f"Player_{index}" for index in range(1, 13)],
        human_seat_ids=set(),
        params={"dry_run": True, "use_runner": True},
    )

    await registry.assign_session(runtime.session.session_id)
    gate = await registry.set_step_mode(runtime.session.session_id, True)
    assert gate["step_mode"] is True

    await registry.start_session(runtime.session.session_id)
    assert runtime.director_task is not None
    await asyncio.sleep(0.05)
    assert not runtime.director_task.done()
    assert runtime.step_gate.status()["waiting_count"] >= 1

    before_pass = runtime.step_gate.status()["pass_count"]
    await registry.step_session(runtime.session.session_id, count=1)
    for _ in range(40):
        if runtime.step_gate.status()["pass_count"] > before_pass:
            break
        await asyncio.sleep(0.05)
    assert runtime.step_gate.status()["pass_count"] > before_pass

    await registry.set_step_mode(runtime.session.session_id, False)
    await asyncio.wait_for(runtime.director_task, timeout=20)
    assert runtime.session.status == "ended"
