"""Free-input mode support for controller actions."""

from __future__ import annotations

from typing import Any

from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.patch.validators import PatchValidator


class FreeInputExecutor:
    """Execute free input modes through plugins or deterministic fallbacks."""

    def __init__(self) -> None:
        """Initialize executor."""
        self._validator = PatchValidator()

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
        selected = choices[0] if choices else {}
        return {
            "kind": "choose_mapping",
            "selected_choice": selected.get("id"),
            "to": selected.get("to"),
            "text": controller_response.get("text", ""),
        }

    def _branch_then_return(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        controller_response: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate a temporary branch record and return target metadata."""
        branch = {
            "type": "temporary_branch",
            "text": controller_response.get("text", ""),
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
        beat = {
            "type": "generated_beat",
            "constrained": constrained,
            "text": controller_response.get("text", "") or "(generated beat)",
            "ending": spec.get("ending") if constrained else None,
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
        patch = spec.get("patch")
        if not isinstance(patch, dict):
            patch = {
                "type": "add_scene",
                "scene": {
                    "id": f"generated_{len(ctx.patch_journal.by_type('flow_patch')) + 1}",
                    "type": "scene",
                    "scope": {"id": "story", "visibility": "public"},
                    "schedule": {"mode": "none"},
                    "participant_action": {"kind": "none"},
                    "controller_action": {
                        "enabled": True,
                        "controller": {"type": "system"},
                        "kind": "narration",
                    },
                    "publication": {
                        "messages": [
                            {
                                "audience": {"scope": "story"},
                                "content": {"text": controller_response.get("text", "(generated scene)")},
                            }
                        ]
                    },
                },
            }
        errors = self._validator.validate_flow_patch(patch)
        if errors:
            raise ValueError(f"flow_patch 校验失败: {errors}")
        ctx.patch_journal.append("flow_patch", patch, {"scene": ctx.current_scene_id})
        return {"kind": "grow_flow", "flow_patch": patch}
