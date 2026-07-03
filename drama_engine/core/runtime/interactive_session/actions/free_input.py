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
            return self._choose_mapping(ctx, spec, controller_response)
        if mode == "branch_then_return":
            return self._branch_then_return(ctx, spec, controller_response)
        if mode == "constrained_continue":
            return self._generated_beat(ctx, spec, controller_response, constrained=True)
        if mode == "free_continue":
            return self._generated_beat(ctx, spec, controller_response, constrained=False)
        if mode == "grow_flow":
            return self._grow_flow(ctx, spec, controller_response)
        raise ValueError(f"未知 free_input.mode: {mode}")

    def _choose_mapping(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        controller_response: dict[str, Any],
    ) -> dict[str, Any]:
        """Map free text to an existing choice."""
        choices = list(spec.get("choices") or [])
        service_result = self._services.call_sync(
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

    def _branch_then_return(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        controller_response: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate a temporary branch record and return target metadata."""
        generator_result = self._services.call_sync(
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
        ctx.patch_journal.append("branch_patch", branch, {"scene": ctx.current_scene_id})
        return {"kind": "branch_then_return", "branch": branch}

    def _generated_beat(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        controller_response: dict[str, Any],
        constrained: bool,
    ) -> dict[str, Any]:
        """Record one generated beat."""
        service_spec = spec.get("generator") or {"name": "story_generator"}
        generator_result = self._services.call_sync(
            ctx,
            service_spec,
            "story_generator",
            {
                **ctx.full_context_payload(),
                "text": controller_response.get("text", ""),
                "constrained": constrained,
                "ending": spec.get("ending"),
            },
        )
        ending = spec.get("ending")
        if constrained and not ending:
            ending_result = self._services.call_sync(
                ctx,
                spec.get("ending_selector") or {"name": "choose_ending_by_progress"},
                "ending_selector",
                ctx.full_context_payload(),
            )
            ending = ending_result.get("ending")
        beat = {
            "type": "generated_beat",
            "constrained": constrained,
            "text": generator_result.get("text") or controller_response.get("text", "") or "(generated beat)",
            "beats": list(generator_result.get("beats") or []),
            "ending": ending if constrained else None,
        }
        ctx.patch_journal.append("story_beat", beat, {"scene": ctx.current_scene_id})
        return {"kind": "constrained_continue" if constrained else "free_continue", "beat": beat}

    def _grow_flow(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        controller_response: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate and store a flow patch."""
        result = self._services.call_sync(
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
        errors = self._validator.validate_flow_patch(patch)
        if errors:
            raise ValueError(f"flow_patch 校验失败: {errors}")
        ctx.patch_journal.append("flow_patch", patch, {"scene": ctx.current_scene_id})
        self._patch_applier.apply(ctx, patch)
        return {"kind": "grow_flow", "flow_patch": patch}

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
