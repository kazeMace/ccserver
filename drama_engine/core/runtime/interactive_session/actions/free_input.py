"""Free-input mode support for controller actions."""

from __future__ import annotations

from typing import Any

from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.patch.applier import FlowPatchApplier
from drama_engine.core.runtime.interactive_session.patch.validators import PatchValidator
from drama_engine.core.runtime.interactive_session.services.runtime_services import RuntimeServiceCaller


class FreeInputExecutor:
    """Execute free input modes through plugins or deterministic fallbacks."""

    def __init__(self) -> None:
        """Initialize executor."""
        self._validator = PatchValidator()
        self._services = RuntimeServiceCaller()
        self._patch_applier = FlowPatchApplier()

    async def execute(
        self,
        ctx: InteractiveExecutionContext,
        mode: str,
        spec: dict[str, Any],
        controller_response: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute one free-input mode."""
        assert mode, "free_input.mode 不能为空"
        if mode == "choose_mapping":
            return await self._choose_mapping(ctx, spec, controller_response)
        if mode == "branch_then_return":
            return await self._branch_then_return(ctx, spec, controller_response)
        if mode == "constrained_continue":
            return await self._generated_beat(ctx, spec, controller_response, constrained=True)
        if mode == "free_continue":
            return await self._generated_beat(ctx, spec, controller_response, constrained=False)
        if mode == "grow_flow":
            return await self._grow_flow(ctx, spec, controller_response)
        raise ValueError(f"未知 free_input.mode: {mode}")

    async def _choose_mapping(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        controller_response: dict[str, Any],
    ) -> dict[str, Any]:
        """Map free text to an existing choice."""
        choices = list(spec.get("choices") or [])
        service_result = await self._services.call_async(
            ctx,
            spec.get("mapper") or {"name": "map_free_text_to_choice"},
            "choose_mapping",
            {
                **ctx.full_context_payload(),
                "text": controller_response.get("text", ""),
                "choices": choices,
            },
        )
        selected_id = service_result.get("selected_choice") or service_result.get("choice_id")
        selected = self._choice_by_id(choices, selected_id)
        if not selected:
            selected = choices[0] if choices else {}
        return {
            "kind": "choose_mapping",
            "selected_choice": selected.get("id"),
            "to": selected.get("to"),
            "text": controller_response.get("text", ""),
            "confidence": service_result.get("confidence"),
        }

    async def _branch_then_return(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        controller_response: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate a temporary branch record and return target metadata."""
        generator_result = await self._services.call_async(
            ctx,
            spec.get("generator") or {"name": "branch_generator"},
            "branch_generator",
            {
                **ctx.full_context_payload(),
                "text": controller_response.get("text", ""),
                "return_to": spec.get("return_to") or {},
            },
        )
        branch = {
            "type": "temporary_branch",
            "text": generator_result.get("text") or controller_response.get("text", ""),
            "beats": list(generator_result.get("beats") or []),
            "return_to": spec.get("return_to") or {},
        }
        flow_patch = self._branch_flow_patch(ctx, spec, generator_result, branch)
        branch_scene_id = self._branch_scene_id(flow_patch)
        if not branch_scene_id:
            raise ValueError("branch_then_return 需要 add_scene flow_patch")
        self._validate_and_preview_flow_patch(ctx, flow_patch, "branch flow_patch")
        branch_record = ctx.patch_journal.append("branch_patch", branch, {"scene": ctx.current_scene_id})
        flow_record = ctx.patch_journal.append("flow_patch", flow_patch, {"scene": ctx.current_scene_id, "branch": True})
        try:
            self._patch_applier.apply(ctx, flow_patch)
        except Exception:
            self._rollback_record(ctx, flow_record.patch_id)
            self._rollback_record(ctx, branch_record.patch_id)
            raise
        return_to = spec.get("return_to") or {}
        if return_to:
            ctx.session_metadata.setdefault("interactive_return_stack", []).append(return_to)
        ctx.session_metadata["interactive_next_target"] = branch_scene_id
        return {"kind": "branch_then_return", "branch": branch, "flow_patch": flow_patch}

    async def _generated_beat(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        controller_response: dict[str, Any],
        constrained: bool,
    ) -> dict[str, Any]:
        """Record one generated beat."""
        max_beats = int(spec.get("max_beats") or spec.get("max_turns") or 1)
        ending = await self._resolve_ending(ctx, spec) if constrained else None
        beat = await self._generate_one_beat(
            ctx=ctx,
            spec=spec,
            controller_response=controller_response,
            constrained=constrained,
            ending=ending,
            beat_index=0,
        )
        result = {"kind": "constrained_continue" if constrained else "free_continue", "beat": beat}
        if spec.get("loop", max_beats > 1) and max_beats > 1:
            result["generation_state"] = {
                "spec": dict(spec),
                "controller_response": dict(controller_response),
                "constrained": constrained,
                "ending": ending,
                "next_index": 1,
                "max_beats": max_beats,
            }
        return result

    async def continue_generated_beat(
        self,
        ctx: InteractiveExecutionContext,
        previous_result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Generate the next beat only after referee allows continuation."""
        state = previous_result.get("generation_state")
        if not isinstance(state, dict):
            return None
        next_index = int(state.get("next_index") or 0)
        max_beats = int(state.get("max_beats") or 0)
        if next_index >= max_beats:
            return None
        spec = dict(state.get("spec") or {})
        controller_response = dict(state.get("controller_response") or {})
        constrained = bool(state.get("constrained"))
        beat = await self._generate_one_beat(
            ctx=ctx,
            spec=spec,
            controller_response=controller_response,
            constrained=constrained,
            ending=state.get("ending"),
            beat_index=next_index,
        )
        next_state = dict(state)
        next_state["next_index"] = next_index + 1
        result = {
            "kind": "constrained_continue" if constrained else "free_continue",
            "beat": beat,
        }
        if next_state["next_index"] < max_beats:
            result["generation_state"] = next_state
        return result

    async def _generate_one_beat(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        controller_response: dict[str, Any],
        constrained: bool,
        ending: Any,
        beat_index: int,
    ) -> dict[str, Any]:
        """Generate and journal exactly one story beat."""
        service_spec = spec.get("generator") or {"name": "story_generator"}
        generator_result = await self._services.call_async(
            ctx,
            service_spec,
            "story_generator",
            {
                **ctx.full_context_payload(),
                "text": controller_response.get("text", ""),
                "constrained": constrained,
                "ending": ending,
                "beat_index": beat_index,
            },
        )
        items = list(generator_result.get("beats") or [])
        item = items[0] if items else {"text": generator_result.get("text") or controller_response.get("text", "")}
        if not isinstance(item, dict):
            item = {"text": str(item)}
        beat = {
            "type": "generated_beat",
            "constrained": constrained,
            "text": str(item.get("text") or ""),
            "beats": [item],
            "ending": ending if constrained else None,
        }
        ctx.patch_journal.append("story_beat", beat, {"scene": ctx.current_scene_id, "beat_index": beat_index})
        return beat

    async def _grow_flow(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        controller_response: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate and store a flow patch."""
        result = await self._services.call_async(
            ctx,
            spec.get("generator") or spec,
            "flow_patch_generator",
            {
                **ctx.full_context_payload(),
                "text": controller_response.get("text", ""),
            },
        )
        patch = result.get("patch") if isinstance(result.get("patch"), dict) else spec.get("patch")
        if not isinstance(patch, dict):
            raise ValueError("grow_flow 需要 generator 返回 patch 或 free_input.patch")
        self._validate_and_preview_flow_patch(ctx, patch, "flow_patch")
        record = ctx.patch_journal.append("flow_patch", patch, {"scene": ctx.current_scene_id})
        try:
            self._patch_applier.apply(ctx, patch)
        except Exception:
            self._rollback_record(ctx, record.patch_id)
            raise
        return {"kind": "grow_flow", "flow_patch": patch}

    def _validate_and_preview_flow_patch(
        self,
        ctx: InteractiveExecutionContext,
        patch: dict[str, Any],
        label: str,
    ) -> None:
        """Validate and dry-run compile a flow patch before journaling."""
        errors = self._validator.validate_flow_patch(patch, ctx.script)
        if errors:
            raise ValueError(f"{label} 校验失败: {errors}")
        self._patch_applier.preview(ctx, patch)

    def _rollback_record(self, ctx: InteractiveExecutionContext, patch_id: str) -> None:
        """Rollback records until the expected patch id has been removed."""
        removed = ctx.patch_journal.rollback_last()
        assert removed is not None and removed.patch_id == patch_id, (
            f"patch journal 回滚顺序错误，期望 {patch_id}"
        )

    def _branch_flow_patch(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        generator_result: dict[str, Any],
        branch: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a temporary branch scene patch."""
        patch = generator_result.get("patch") or generator_result.get("flow_patch") or spec.get("patch")
        if isinstance(patch, dict):
            patch.setdefault("after", ctx.current_scene_id)
            return patch
        scene_id = f"branch_{len(ctx.patch_journal.by_type('branch_patch')) + 1}"
        text = branch.get("text") or "支线剧情展开。"
        return {
            "type": "add_scene",
            "after": ctx.current_scene_id,
            "scene": {
                "id": scene_id,
                "type": "scene",
                "scope": {"id": "story", "visibility": "public"},
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
                "controller_action": {"enabled": False, "kind": "none"},
                "publication": {
                    "messages": [
                        {
                            "audience": {"scope": "story"},
                            "content": {"text": text},
                        }
                    ]
                },
            },
        }

    def _branch_scene_id(self, patch: dict[str, Any]) -> str:
        """Return the generated branch scene id, or empty when invalid."""
        if patch.get("type") != "add_scene":
            return ""
        scene = patch.get("scene")
        if not isinstance(scene, dict):
            return ""
        return str(scene.get("id") or scene.get("name") or "")

    async def _resolve_ending(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
    ) -> Any:
        """Resolve constrained ending using ending.selector or ending_selector."""
        ending_spec = spec.get("ending")
        if isinstance(ending_spec, dict):
            selector = ending_spec.get("selector") or spec.get("ending_selector")
            selector = selector or {"name": "choose_ending_by_progress"}
            result = await self._services.call_async(
                ctx,
                selector,
                "ending_selector",
                {**ctx.full_context_payload(), "ending": ending_spec},
            )
            return result.get("ending") or result.get("selected") or ending_spec.get("default")
        if ending_spec:
            return ending_spec
        result = await self._services.call_async(
            ctx,
            spec.get("ending_selector") or {"name": "choose_ending_by_progress"},
            "ending_selector",
            ctx.full_context_payload(),
        )
        return result.get("ending")

    def _choice_by_id(
        self,
        choices: list[dict[str, Any]],
        choice_id: Any,
    ) -> dict[str, Any]:
        """Return choice matching id, or empty dict."""
        if choice_id is None:
            return {}
        for choice in choices:
            if str(choice.get("id")) == str(choice_id):
                return choice
        return {}
