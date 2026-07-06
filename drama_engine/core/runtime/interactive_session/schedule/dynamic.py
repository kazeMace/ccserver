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
        visibility = "private"
        scope_visibility = allowed.get("scope_visibility")
        if isinstance(scope_visibility, list) and scope_visibility:
            visibility = str(scope_visibility[0])
        result.setdefault("scope", {
            "id": "dynamic_" + "_".join(str(item) for item in result["participants"]),
            "visibility": visibility,
            "members": list(result["participants"]),
        })
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
        participants = [
            str(name)
            for name in patch.get("participants", [])
            if str(name) in parent_participants or str(name) in ctx.cast.all_names()
        ]
        scope_spec = patch.get("scope") or {}
        scope = ScopeSpec(
            id=str(scope_spec.get("id") or "dynamic_scope"),
            visibility=str(scope_spec.get("visibility") or "private"),
            members=[str(item) for item in scope_spec.get("members", participants) or participants],
        )
        schedule = ScheduleSpec(
            mode=str(patch.get("mode") or "openchat"),
            max_turns=int(patch.get("max_turns") or 1),
            max_rounds=int(patch.get("max_rounds") or 1),
        )
        responses = []
        rounds = schedule.max_turns if schedule.mode == "openchat" else 1
        for _ in range(max(1, rounds)):
            responses.extend(await self._participant_actions.collect_many(
                ctx=ctx,
                actor_names=participants,
                action=parent_action,
                scope=scope,
                participants=participants,
                mode=schedule.mode,
                cue="临时子对话，请根据当前私密上下文发言。",
            ))
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
            self._merge_back(ctx, merge_back, responses, patch)
            ctx.emit_host({
                "kind": "interactive_schedule_merge",
                "scene": ctx.current_scene_id,
                "merge_back": merge_back,
                "responses": responses,
            })
        return responses

    def _merge_back(
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
            service_result = self._services.call_sync(
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
