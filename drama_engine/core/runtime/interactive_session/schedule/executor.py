"""Schedule executor for interactive_session."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.participant import ParticipantActionExecutor
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.models import (
    ParticipantActionSpec,
    ScheduleSpec,
    ScopeSpec,
)
from drama_engine.core.runtime.interactive_session.schedule.dynamic import DynamicScheduleExecutor
from drama_engine.core.runtime.interactive_session.schedule.modes import ScheduleModePlanner
from drama_engine.core.runtime.interactive_session.services.runtime_services import RuntimeServiceCaller


class ScheduleExecutor:
    """Execute a scene schedule and collect participant responses."""

    def __init__(
        self,
        participant_actions: ParticipantActionExecutor | None = None,
        dynamic: DynamicScheduleExecutor | None = None,
    ) -> None:
        """Initialize schedule executor."""
        self._participant_actions = participant_actions or ParticipantActionExecutor()
        self._dynamic = dynamic or DynamicScheduleExecutor(self._participant_actions)
        self._planner = ScheduleModePlanner()
        self._services = RuntimeServiceCaller()

    async def execute(
        self,
        ctx: InteractiveExecutionContext,
        schedule: ScheduleSpec,
        action: ParticipantActionSpec,
        scope: ScopeSpec,
        participants: list[str],
        cue: str = "",
        after_response: Callable[[dict[str, Any], list[dict[str, Any]]], Awaitable[str | None]] | None = None,
        after_round: Callable[[dict[str, Any], list[dict[str, Any]]], Awaitable[str | None]] | None = None,
        on_schedule_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Execute schedule and return collected responses plus early result."""
        if schedule.mode == "none":
            return {"responses": [], "result": None}
        if schedule.mode == "openchat":
            return await self._execute_openchat(
                ctx,
                schedule,
                action,
                scope,
                participants,
                cue,
                after_response,
                after_round,
                on_schedule_event,
            )
        responses = []
        for _round_index in range(self._planner.rounds(schedule)):
            actor_order = self._planner.order(ctx, participants, schedule)
            round_responses: list[dict[str, Any]] = []
            if schedule.mode == "simultaneous":
                round_responses = await self._participant_actions.collect_many(
                    ctx=ctx,
                    actor_names=actor_order,
                    action=action,
                    scope=scope,
                    participants=participants,
                    mode=schedule.mode,
                    cue=cue,
                    timeout_ms=schedule.timeout_ms,
                )
                for response in round_responses:
                    responses.append(response)
                    result = await self._handle_one_response(
                        ctx,
                        schedule,
                        action,
                        participants,
                        responses,
                        response,
                        after_response,
                        on_schedule_event,
                    )
                    if result is not None:
                        return {"responses": responses, "result": result}
            else:
                for actor_name in actor_order:
                    actor_responses = await self._participant_actions.collect_many(
                        ctx=ctx,
                        actor_names=[actor_name],
                        action=action,
                        scope=scope,
                        participants=participants,
                        mode=schedule.mode,
                        cue=cue,
                        timeout_ms=schedule.timeout_ms,
                    )
                    for response in actor_responses:
                        round_responses.append(response)
                        responses.append(response)
                        result = await self._handle_one_response(
                            ctx,
                            schedule,
                            action,
                            participants,
                            responses,
                            response,
                            after_response,
                            on_schedule_event,
                        )
                        if result is not None:
                            return {"responses": responses, "result": result}
            result = await self._handle_round_event(after_round, round_responses, responses)
            if result is not None:
                return {"responses": responses, "result": result}
            if schedule.dynamic.check_on == "after_round":
                child_result = await self._dynamic.maybe_run(
                    ctx=ctx,
                    dynamic=schedule.dynamic,
                    parent_action=action,
                    parent_participants=participants,
                    source_response={
                        "kind": "round_completed",
                        "data": {"responses": list(round_responses)},
                        "text": "",
                    },
                    after_response=after_response,
                    parent_responses=list(responses),
                    on_schedule_event=on_schedule_event,
                )
                child_responses = list(child_result.get("responses") or [])
                if child_responses:
                    responses.extend(child_responses)
                    ctx.last_responses = list(responses)
                if child_result.get("result") is not None:
                    return {"responses": responses, "result": child_result.get("result")}
            if await self._should_stop(ctx, schedule):
                break
        return {"responses": responses, "result": None}

    async def _handle_one_response(
        self,
        ctx: InteractiveExecutionContext,
        schedule: ScheduleSpec,
        action: ParticipantActionSpec,
        participants: list[str],
        responses: list[dict[str, Any]],
        response: dict[str, Any],
        after_response: Callable[[dict[str, Any], list[dict[str, Any]]], Awaitable[str | None]] | None,
        on_schedule_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> str | None:
        """Record and process one participant response."""
        ctx.last_responses = list(responses)
        ctx.record_message({
            "kind": "interactive_message",
            "runtime_type": "interactive_session",
            "scene": ctx.current_scene_id,
            "sender": response.get("actor"),
            "text": response.get("text", ""),
            "data": response.get("data"),
        })
        if after_response is not None:
            result = await after_response(response, list(responses))
            if result is not None:
                return result
        if schedule.dynamic.check_on == "after_message":
            child_result = await self._dynamic.maybe_run(
                ctx=ctx,
                dynamic=schedule.dynamic,
                parent_action=action,
                parent_participants=participants,
                source_response=response,
                after_response=after_response,
                parent_responses=list(responses),
                on_schedule_event=on_schedule_event,
            )
            child_responses = list(child_result.get("responses") or [])
            if child_responses:
                responses.extend(child_responses)
                ctx.last_responses = list(responses)
            if child_result.get("result") is not None:
                return str(child_result["result"])
        return None

    async def _execute_openchat(
        self,
        ctx: InteractiveExecutionContext,
        schedule: ScheduleSpec,
        action: ParticipantActionSpec,
        scope: ScopeSpec,
        participants: list[str],
        cue: str,
        after_response: Callable[[dict[str, Any], list[dict[str, Any]]], Awaitable[str | None]] | None,
        after_round: Callable[[dict[str, Any], list[dict[str, Any]]], Awaitable[str | None]] | None,
        on_schedule_event: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> dict[str, Any]:
        """Execute open chat one speaker at a time with planner support."""
        responses = []
        current_actor = self._planner.openchat_first_actor(ctx, participants, schedule)
        current_cue = str(schedule.opening or cue or "")
        for turn_index in range(max(1, schedule.max_turns)):
            if not current_actor:
                break
            round_responses = await self._participant_actions.collect_many(
                ctx=ctx,
                actor_names=[current_actor],
                action=action,
                scope=scope,
                participants=participants,
                mode=schedule.mode,
                cue=current_cue,
                timeout_ms=schedule.timeout_ms,
            )
            if not round_responses:
                break
            for response in round_responses:
                responses.append(response)
                result = await self._handle_one_response(
                    ctx,
                    schedule,
                    action,
                    participants,
                    responses,
                    response,
                    after_response,
                    on_schedule_event,
                )
                if result is not None:
                    return {"responses": responses, "result": result}
            result = await self._handle_round_event(after_round, round_responses, responses)
            if result is not None:
                return {"responses": responses, "result": result}
            if schedule.dynamic.check_on == "after_round":
                child_result = await self._dynamic.maybe_run(
                    ctx=ctx,
                    dynamic=schedule.dynamic,
                    parent_action=action,
                    parent_participants=participants,
                    source_response={
                        "kind": "round_completed",
                        "data": {"responses": list(round_responses)},
                        "text": "",
                    },
                    after_response=after_response,
                    parent_responses=list(responses),
                    on_schedule_event=on_schedule_event,
                )
                child_responses = list(child_result.get("responses") or [])
                if child_responses:
                    responses.extend(child_responses)
                    ctx.last_responses = list(responses)
                if child_result.get("result") is not None:
                    return {"responses": responses, "result": child_result.get("result")}
            if await self._should_stop(ctx, schedule):
                break
            plan = await self._plan_openchat_next(
                ctx=ctx,
                schedule=schedule,
                participants=participants,
                responses=responses,
                turn_index=turn_index,
                fallback_actor=current_actor,
            )
            if plan.get("stop") is True:
                break
            current_actor = str(plan.get("next_speaker") or "")
            current_cue = str(plan.get("cue") or cue or "")
        return {"responses": responses, "result": None}

    async def _handle_round_event(
        self,
        after_round: Callable[[dict[str, Any], list[dict[str, Any]]], Awaitable[str | None]] | None,
        round_responses: list[dict[str, Any]],
        current_responses: list[dict[str, Any]],
    ) -> str | None:
        """Run the caller's after-round hook with the latest response view."""
        if after_round is None:
            return None
        event = {
            "kind": "round_completed",
            "data": {
                "responses": list(round_responses),
                "current_responses": list(current_responses),
            },
            "text": "",
        }
        return await after_round(event, list(current_responses))

    async def _plan_openchat_next(
        self,
        ctx: InteractiveExecutionContext,
        schedule: ScheduleSpec,
        participants: list[str],
        responses: list[dict],
        turn_index: int,
        fallback_actor: str,
    ) -> dict:
        """Plan the next openchat speaker through service or round-robin."""
        service_spec = schedule.planner or {}
        if not service_spec and isinstance(schedule.order, dict):
            executor_name = schedule.order.get("executor")
            if executor_name:
                service_spec = dict(schedule.order)
        if service_spec:
            result = await self._services.call_async(
                ctx,
                service_spec,
                "openchat_planner",
                {
                    **ctx.full_context_payload(),
                    "participants": list(participants),
                    "responses": list(responses),
                    "last_response": responses[-1] if responses else {},
                    "turn_index": turn_index,
                },
            )
            next_speaker = result.get("next_speaker") or result.get("speaker") or result.get("actor")
            if next_speaker in participants:
                return {
                    "next_speaker": str(next_speaker),
                    "cue": result.get("cue") or result.get("opening") or "",
                    "stop": bool(result.get("stop", False)),
                }
            if result.get("stop") is True:
                return {"stop": True}
        if not participants:
            return {"stop": True}
        index = participants.index(fallback_actor) if fallback_actor in participants else -1
        return {"next_speaker": participants[(index + 1) % len(participants)], "cue": "", "stop": False}

    async def _should_stop(
        self,
        ctx: InteractiveExecutionContext,
        schedule: ScheduleSpec,
    ) -> bool:
        """Check schedule.stop_when."""
        if not schedule.stop_when:
            return False
        try:
            return await ctx.condition_evaluator.evaluate_async(
                schedule.stop_when,
                ctx.state,
                actor=None,
                responses=ctx.last_responses,
                extra=ctx.condition_extra(),
            )
        except Exception as exc:  # noqa: BLE001 - keep the session observable.
            ctx.emit_host({
                "kind": "interactive_session_warning",
                "message": f"schedule.stop_when 求值失败: {exc}",
                "scene": ctx.current_scene_id,
            })
            return False
