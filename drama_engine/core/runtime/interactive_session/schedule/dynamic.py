"""Dynamic schedule support."""

from __future__ import annotations

from typing import Any

from drama_engine.core.engine import SetAttr
from drama_engine.core.runtime.interactive_session.actions.participant import ParticipantActionExecutor
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.models import (
    DynamicScheduleSpec,
    ParticipantActionSpec,
    ScheduleSpec,
    ScopeSpec,
)
from drama_engine.core.runtime.interactive_session.patch.validators import PatchValidator
from drama_engine.core.runtime.interactive_session.services.runtime_services import RuntimeServiceCaller


class DynamicScheduleExecutor:
    """Create and execute temporary child schedule frames."""

    def __init__(self, participant_actions: ParticipantActionExecutor | None = None) -> None:
        """Initialize dynamic schedule executor."""
        self._participant_actions = participant_actions or ParticipantActionExecutor()
        self._validator = PatchValidator()
        self._services = RuntimeServiceCaller()

    async def maybe_run(
        self,
        ctx: InteractiveExecutionContext,
        dynamic: DynamicScheduleSpec,
        parent_action: ParticipantActionSpec,
        parent_participants: list[str],
        source_response: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Check detector output and run a child schedule when requested."""
        if not dynamic.enabled:
            return []
        patch = await self._detect_patch(ctx, dynamic, parent_participants, source_response)
        if not patch:
            return []
        errors = self._validator.validate_schedule_patch(patch)
        if errors:
            ctx.emit_host({
                "kind": "interactive_session_warning",
                "message": f"schedule_patch 校验失败: {errors}",
                "scene": ctx.current_scene_id,
            })
            return []
        if patch.get("type") == "pop_schedule":
            ctx.patch_journal.append("schedule_patch", patch, {"scene": ctx.current_scene_id})
            return []
        return await self._run_child_schedule(
            ctx,
            patch,
            dynamic,
            parent_action,
            parent_participants,
            source_response,
        )

    async def _detect_patch(
        self,
        ctx: InteractiveExecutionContext,
        dynamic: DynamicScheduleSpec,
        parent_participants: list[str],
        source_response: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Detect requested schedule patch through service or response data."""
        detector_patch = dynamic.detector.get("patch") if isinstance(dynamic.detector, dict) else None
        if isinstance(detector_patch, dict):
            return self._apply_allowed_defaults(detector_patch, dynamic, parent_participants)
        data = source_response.get("data")
        if isinstance(data, dict) and isinstance(data.get("schedule_patch"), dict):
            return self._apply_allowed_defaults(data["schedule_patch"], dynamic, parent_participants)
        detector_result = await self._services.call_async(
            ctx,
            dynamic.detector or {"name": "detect_schedule_request"},
            "schedule_detector",
            {
                **ctx.full_context_payload(),
                "source_response": source_response,
                "parent_participants": list(parent_participants),
            },
        )
        result_patch = detector_result.get("patch")
        if isinstance(result_patch, dict):
            return self._apply_allowed_defaults(result_patch, dynamic, parent_participants)
        return None

    def _apply_allowed_defaults(
        self,
        patch: dict[str, Any],
        dynamic: DynamicScheduleSpec,
        parent_participants: list[str],
    ) -> dict[str, Any]:
        """Apply defaults and allowed constraints to a patch."""
        result = dict(dynamic.patch or {})
        result.update(dict(patch))
        result.setdefault("type", "push_schedule")
        result.setdefault("mode", "openchat")
        if "participants" not in result:
            result["participants"] = parent_participants[:2]
        allowed = dynamic.allowed or {}
        allowed_modes = allowed.get("modes")
        if isinstance(allowed_modes, list) and result["mode"] not in allowed_modes:
            result["__invalid_reason"] = f"mode {result['mode']} 不在 allowed.modes 中"
            return result
        participant_count = allowed.get("participant_count")
        if isinstance(participant_count, dict):
            count = len(result.get("participants", []) or [])
            min_count = int(participant_count.get("min") or 0)
            max_count = int(participant_count.get("max") or count)
            if count < min_count or count > max_count:
                result["__invalid_reason"] = (
                    f"participants 数量 {count} 不在 {min_count}..{max_count} 范围内"
                )
                return result
        max_turns = allowed.get("max_turns") if isinstance(allowed.get("max_turns"), dict) else {}
        result.setdefault("max_turns", int(max_turns.get("default") or 1))
        if max_turns.get("max") is not None and int(result["max_turns"]) > int(max_turns["max"]):
            result["__invalid_reason"] = f"max_turns {result['max_turns']} 超过 allowed.max_turns.max"
            return result
        visibility = "public" if result.get("mode") == "openchat" else "private"
        scope_visibility = allowed.get("scope_visibility")
        if isinstance(scope_visibility, list) and scope_visibility:
            allowed_values = [str(item) for item in scope_visibility]
            if visibility not in allowed_values:
                visibility = allowed_values[0]
        default_scope = {
            "id": "dynamic_" + "_".join(str(item) for item in result["participants"]),
            "visibility": visibility,
            "members": list(result["participants"]),
        }
        scope_value = result.get("scope")
        if isinstance(scope_value, dict):
            result["scope"] = {**default_scope, **scope_value}
        else:
            result["scope"] = default_scope
        scope = result.get("scope") or {}
        if isinstance(scope_visibility, list) and scope.get("visibility") not in scope_visibility:
            result["__invalid_reason"] = (
                f"scope.visibility {scope.get('visibility')} 不在 allowed.scope_visibility 中"
            )
            return result
        return result

    async def _run_child_schedule(
        self,
        ctx: InteractiveExecutionContext,
        patch: dict[str, Any],
        dynamic: DynamicScheduleSpec,
        parent_action: ParticipantActionSpec,
        parent_participants: list[str],
        source_response: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Execute a pushed child schedule immediately."""
        participants = [
            str(name)
            for name in patch.get("participants", [])
            if str(name) in parent_participants or str(name) in ctx.cast.all_names()
        ]
        if not participants:
            ctx.emit_host({
                "kind": "interactive_session_warning",
                "message": "schedule_patch 参与者不在当前 actor 集合中",
                "scene": ctx.current_scene_id,
                "patch": patch,
            })
            return []
        scope_spec = patch.get("scope") or {}
        scope = ScopeSpec(
            id=str(scope_spec.get("id") or "dynamic_scope"),
            visibility=str(scope_spec.get("visibility") or "private"),
            members=[str(item) for item in scope_spec.get("members", participants) or participants],
        )
        schedule = ScheduleSpec(
            mode=str(patch.get("mode") or "openchat"),
            actor=patch.get("first_speaker") or patch.get("actor"),
            planner=dict(patch.get("planner") or {}),
            opening=patch.get("opening") or patch.get("cue") or "",
            max_turns=int(patch.get("max_turns") or 1),
            max_rounds=int(patch.get("max_rounds") or 1),
            timeout_ms=patch.get("timeout_ms"),
            stop_when=patch.get("stop_when") or patch.get("until"),
        )
        ctx.patch_journal.append(
            "schedule_patch",
            patch,
            {"scene": ctx.current_scene_id, "source_response": source_response},
        )
        ctx.emit_public({
            "kind": "interactive_schedule_pushed",
            "runtime_type": "interactive_session",
            "scene": ctx.current_scene_id,
            "patch": patch,
        })
        responses = []
        cue = str(schedule.opening or "临时子对话，请根据当前私密上下文发言。")
        if schedule.mode == "openchat":
            responses = await self._run_openchat_child(
                ctx,
                schedule,
                parent_action,
                scope,
                participants,
                cue,
                patch,
                source_response,
            )
        else:
            actor_order = participants
            for _round_index in range(max(1, schedule.max_rounds)):
                responses.extend(await self._participant_actions.collect_many(
                    ctx=ctx,
                    actor_names=actor_order,
                    action=parent_action,
                    scope=scope,
                    participants=participants,
                    mode=schedule.mode,
                    cue=cue,
                    timeout_ms=schedule.timeout_ms,
                ))
                ctx.last_responses = list(responses)
                if await self._should_stop(ctx, schedule):
                    break
        pop_patch = {"type": "pop_schedule", "parent_scene": ctx.current_scene_id}
        ctx.patch_journal.append("schedule_patch", pop_patch, {"scene": ctx.current_scene_id})
        ctx.emit_public({
            "kind": "interactive_schedule_popped",
            "runtime_type": "interactive_session",
            "scene": ctx.current_scene_id,
            "patch": pop_patch,
        })
        merge_back = patch.get("merge_back") or dynamic.merge_back
        if merge_back:
            await self._merge_back(ctx, merge_back, responses, patch)
            ctx.emit_host({
                "kind": "interactive_schedule_merge",
                "scene": ctx.current_scene_id,
                "merge_back": merge_back,
                "responses": responses,
            })
        return responses

    async def _run_openchat_child(
        self,
        ctx: InteractiveExecutionContext,
        schedule: ScheduleSpec,
        parent_action: ParticipantActionSpec,
        scope: ScopeSpec,
        participants: list[str],
        cue: str,
        patch: dict[str, Any],
        source_response: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Run a dynamic openchat child schedule through planner decisions."""
        responses: list[dict[str, Any]] = []
        current_actor = self._resolve_first_openchat_actor(participants, schedule.actor)
        current_cue = cue
        for turn_index in range(max(1, schedule.max_turns)):
            if not current_actor:
                break
            round_responses = await self._participant_actions.collect_many(
                ctx=ctx,
                actor_names=[current_actor],
                action=parent_action,
                scope=scope,
                participants=participants,
                mode=schedule.mode,
                cue=current_cue,
                timeout_ms=schedule.timeout_ms,
            )
            if not round_responses:
                break
            responses.extend(round_responses)
            ctx.last_responses = list(responses)
            if await self._should_stop(ctx, schedule):
                break
            plan = await self._plan_openchat_next(
                ctx,
                schedule,
                participants,
                responses,
                turn_index,
                current_actor,
                patch,
                source_response,
            )
            if plan.get("stop") is True:
                break
            current_actor = str(plan.get("next_speaker") or "")
            current_cue = str(plan.get("cue") or cue)
        return responses

    async def _plan_openchat_next(
        self,
        ctx: InteractiveExecutionContext,
        schedule: ScheduleSpec,
        participants: list[str],
        responses: list[dict[str, Any]],
        turn_index: int,
        fallback_actor: str,
        patch: dict[str, Any],
        source_response: dict[str, Any],
    ) -> dict[str, Any]:
        """Ask the patch planner for the next child openchat speaker."""
        service_spec = schedule.planner or {}
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
                    "schedule_patch": dict(patch),
                    "source_response": dict(source_response),
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
        return self._round_robin_plan(participants, fallback_actor)

    async def _should_stop(
        self,
        ctx: InteractiveExecutionContext,
        schedule: ScheduleSpec,
    ) -> bool:
        """Check child schedule stop_when."""
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
        except Exception as exc:  # noqa: BLE001 - child schedule must stay observable.
            ctx.emit_host({
                "kind": "interactive_session_warning",
                "message": f"dynamic schedule.stop_when 求值失败: {exc}",
                "scene": ctx.current_scene_id,
            })
            return False

    def _resolve_first_openchat_actor(self, participants: list[str], first_speaker: Any) -> str | None:
        """Resolve the first child openchat speaker."""
        if not participants:
            return None
        first = str(first_speaker or participants[0])
        if first not in participants:
            return participants[0]
        return first

    def _round_robin_plan(self, participants: list[str], fallback_actor: str) -> dict[str, Any]:
        """Choose the next speaker by stable round-robin fallback."""
        if not participants:
            return {"stop": True}
        index = participants.index(fallback_actor) if fallback_actor in participants else -1
        return {"next_speaker": participants[(index + 1) % len(participants)], "cue": "", "stop": False}

    async def _merge_back(
        self,
        ctx: InteractiveExecutionContext,
        merge_back: dict[str, Any],
        responses: list[dict[str, Any]],
        patch: dict[str, Any],
    ) -> None:
        """Merge child schedule results into runtime state."""
        assert isinstance(merge_back, dict), "merge_back 必须是 dict"
        target = str(merge_back.get("to") or "")
        if not target:
            return
        mode = str(merge_back.get("mode") or "summary")
        value: Any
        if mode == "plugin":
            service_result = await self._services.call_async(
                ctx,
                merge_back.get("plugin") or merge_back.get("service") or {"provider": "plugin", **merge_back},
                "schedule_merge_back",
                {
                    **ctx.full_context_payload(),
                    "responses": list(responses),
                    "schedule_patch": dict(patch),
                    "merge_back": dict(merge_back),
                },
            )
            value = service_result.get("value", service_result)
        else:
            value = self._summary_value(responses, patch)
        if "." not in target:
            raise ValueError("merge_back.to 必须是 ENTITY.attr 格式")
        entity, attr = target.split(".", 1)
        if not ctx.state.has_entity(entity):
            ctx.state.register_entity(entity, {})
        ctx.writer.apply(SetAttr(entity, attr, value))

    def _openchat_child_order(
        self,
        participants: list[str],
        first_speaker: Any,
        max_turns: int,
    ) -> list[str]:
        """Build child openchat speaker order with an optional first speaker."""
        if not participants:
            return []
        first = str(first_speaker or participants[0])
        if first not in participants:
            first = participants[0]
        start = participants.index(first)
        return [participants[(start + index) % len(participants)] for index in range(max(1, max_turns))]

    def _summary_value(
        self,
        responses: list[dict[str, Any]],
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a deterministic child schedule summary."""
        texts = []
        for response in responses:
            text = str(response.get("text") or "")
            if text:
                texts.append(text)
        return {
            "mode": "summary",
            "participants": list(patch.get("participants") or []),
            "response_count": len(responses),
            "text": "\n".join(texts),
            "responses": list(responses),
        }
