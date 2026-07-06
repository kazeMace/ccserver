"""Controller action executor."""

from __future__ import annotations

from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input import FreeInputExecutor
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.models import ControllerActionSpec
from drama_engine.core.runtime.interactive_session.services.runtime_services import RuntimeServiceCaller


class ControllerActionExecutor:
    """Execute story-controller actions."""

    def __init__(self) -> None:
        """Initialize executor."""
        self._free_input = FreeInputExecutor()
        self._services = RuntimeServiceCaller()

    async def execute(
        self,
        ctx: InteractiveExecutionContext,
        action: ControllerActionSpec,
    ) -> dict[str, Any] | None:
        """Execute controller action if enabled."""
        if not action.enabled or action.kind == "none":
            return None
        if action.kind == "narration":
            return self._narration(ctx, action)
        if action.kind == "choice":
            return await self._choice(ctx, action)
        if action.kind == "free_text":
            return await self._free_text(ctx, action)
        raise ValueError(f"未知 controller_action.kind: {action.kind}")

    def _narration(
        self,
        ctx: InteractiveExecutionContext,
        action: ControllerActionSpec,
    ) -> dict[str, Any]:
        """Emit a narration event."""
        event = {
            "kind": "interactive_controller_narration",
            "runtime_type": "interactive_session",
            "scene": ctx.current_scene_id,
            "controller": action.controller,
        }
        ctx.emit_public(event)
        return event

    async def _choice(
        self,
        ctx: InteractiveExecutionContext,
        action: ControllerActionSpec,
    ) -> dict[str, Any]:
        """Choose one option through controller or fallback."""
        response = await self._controller_response(ctx, action, "请选择一个选项。")
        free_input = dict(action.free_input or {})
        if free_input.get("enabled"):
            free_input["choices"] = action.choices
            result = await self._free_input.execute(
                ctx,
                str(free_input.get("mode") or "choose_mapping"),
                free_input,
                response,
            )
        else:
            selected = self._selected_choice_from_response(action.choices, response)
            result = {
                "kind": "choice",
                "selected_choice": selected.get("id"),
                "to": selected.get("to"),
                "text": response.get("text", ""),
            }
        self._apply_choice_target(ctx, result)
        ctx.emit_public({
            "kind": "interactive_controller_choice",
            "runtime_type": "interactive_session",
            "scene": ctx.current_scene_id,
            "result": result,
        })
        return result

    async def _free_text(
        self,
        ctx: InteractiveExecutionContext,
        action: ControllerActionSpec,
    ) -> dict[str, Any]:
        """Collect free text and execute its configured mode."""
        response = await self._controller_response(ctx, action, "请继续推动剧情。")
        free_input = dict(action.free_input or {})
        mode = str(free_input.get("mode") or "free_continue")
        if free_input.get("enabled", True):
            result = await self._free_input.execute(ctx, mode, free_input, response)
        else:
            result = {"kind": "free_text", "text": response.get("text", "")}
        ctx.emit_public({
            "kind": "interactive_controller_free_text",
            "runtime_type": "interactive_session",
            "scene": ctx.current_scene_id,
            "result": result,
        })
        return result

    async def _controller_response(
        self,
        ctx: InteractiveExecutionContext,
        action: ControllerActionSpec,
        cue: str,
    ) -> dict[str, Any]:
        """Collect a controller response from human/agent/system/plugin fallback."""
        controller = dict(action.controller or {})
        controller_type = str(controller.get("type") or "none")
        if controller_type in {"human", "agent"}:
            actor_name = str(controller.get("agent_id") or controller.get("seat_id") or "")
            if not actor_name:
                actor_name = self._default_controller_actor(ctx, controller_type)
            if actor_name in ctx.cast.all_names():
                return await ctx.cast.get(actor_name).act(cue, None)
        if controller_type == "plugin":
            service_result = await self._services.call_async(
                ctx,
                controller,
                "controller",
                {**ctx.full_context_payload(), "cue": cue},
            )
            return {
                "actor": str(controller.get("name") or "plugin"),
                "text": str(service_result.get("text") or ""),
                "data": service_result.get("data"),
            }
        return {"actor": controller_type, "text": "(system controller)", "data": None}

    async def continue_generated_beat(
        self,
        ctx: InteractiveExecutionContext,
        previous_result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Continue generated story beats after referee allows it."""
        return await self._free_input.continue_generated_beat(ctx, previous_result)

    def _default_controller_actor(
        self,
        ctx: InteractiveExecutionContext,
        controller_type: str,
    ) -> str:
        """Resolve a default actor for human or agent controller types."""
        names = ctx.cast.all_names()
        if not names:
            return ""
        if controller_type == "human":
            human_seats = set(ctx.session_metadata.get("human_seat_ids") or [])
            for name in names:
                actor = ctx.cast.get(name)
                if name in human_seats or getattr(actor, "is_human", False):
                    return str(name)
        return str(names[0])

    def _apply_choice_target(
        self,
        ctx: InteractiveExecutionContext,
        result: dict[str, Any],
    ) -> None:
        """Store requested next scene/state for flow executor."""
        target = result.get("to")
        if not target:
            return
        ctx.session_metadata["interactive_next_target"] = str(target)

    def _selected_choice_from_response(
        self,
        choices: list[dict[str, Any]],
        response: dict[str, Any],
    ) -> dict[str, Any]:
        """Select choice from structured response or text."""
        if not choices:
            return {}
        data = response.get("data")
        selected_id = None
        if isinstance(data, dict):
            selected_id = data.get("choose") or data.get("choice") or data.get("choice_id")
        if selected_id is not None:
            for choice in choices:
                if str(choice.get("id")) == str(selected_id):
                    return choice
        text = str(response.get("text") or "").lower()
        for choice in choices:
            choice_id = str(choice.get("id") or "").lower()
            choice_text = str(choice.get("text") or "").lower()
            if text and (choice_id in text or choice_text in text):
                return choice
        return choices[0]
