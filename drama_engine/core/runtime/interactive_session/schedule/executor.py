"""Schedule executor for interactive_session."""

from __future__ import annotations

from drama_engine.core.runtime.interactive_session.actions.participant import ParticipantActionExecutor
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.models import (
    ParticipantActionSpec,
    ScheduleSpec,
    ScopeSpec,
)
from drama_engine.core.runtime.interactive_session.schedule.dynamic import DynamicScheduleExecutor
from drama_engine.core.runtime.interactive_session.schedule.modes import ScheduleModePlanner


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

    async def execute(
        self,
        ctx: InteractiveExecutionContext,
        schedule: ScheduleSpec,
        action: ParticipantActionSpec,
        scope: ScopeSpec,
        participants: list[str],
        cue: str = "",
    ) -> list[dict]:
        """Execute schedule and return collected responses."""
        if schedule.mode == "none":
            return []
        responses = []
        for _round_index in range(self._planner.rounds(schedule)):
            actor_order = self._planner.order(ctx, participants, schedule)
            round_responses = await self._participant_actions.collect_many(
                ctx=ctx,
                actor_names=actor_order,
                action=action,
                scope=scope,
                participants=participants,
                mode=schedule.mode,
                cue=cue,
            )
            responses.extend(round_responses)
            ctx.last_responses = list(responses)
            if schedule.dynamic.check_on == "after_message":
                for response in round_responses:
                    child_responses = await self._dynamic.maybe_run(
                        ctx=ctx,
                        dynamic=schedule.dynamic,
                        parent_action=action,
                        parent_participants=participants,
                        source_response=response,
                    )
                    if child_responses:
                        responses.extend(child_responses)
                        ctx.last_responses = list(responses)
            elif schedule.dynamic.check_on == "after_round":
                child_responses = await self._dynamic.maybe_run(
                    ctx=ctx,
                    dynamic=schedule.dynamic,
                    parent_action=action,
                    parent_participants=participants,
                    source_response={
                        "kind": "round_completed",
                        "data": {"responses": list(round_responses)},
                        "text": "",
                    },
                )
                if child_responses:
                    responses.extend(child_responses)
                    ctx.last_responses = list(responses)
            if self._should_stop(ctx, schedule):
                break
        return responses

    def _should_stop(
        self,
        ctx: InteractiveExecutionContext,
        schedule: ScheduleSpec,
    ) -> bool:
        """Check schedule.stop_when."""
        if not schedule.stop_when:
            return False
        try:
            return ctx.condition_evaluator.evaluate(
                schedule.stop_when,
                ctx.state,
                actor=None,
                responses=ctx.last_responses,
                extra=ctx.runtime_extra(),
            )
        except Exception as exc:  # noqa: BLE001 - keep the session observable.
            ctx.emit_host({
                "kind": "interactive_session_warning",
                "message": f"schedule.stop_when 求值失败: {exc}",
                "scene": ctx.current_scene_id,
            })
            return False
