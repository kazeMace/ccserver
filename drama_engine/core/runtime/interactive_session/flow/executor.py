"""Flow executor for interactive_session."""

from __future__ import annotations

from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.models import FlowStateSpec
from drama_engine.core.runtime.interactive_session.scene.executor import SceneExecutor


class FlowExecutor:
    """Execute sequence/state_machine flow specs."""

    def __init__(self, scene_executor: SceneExecutor | None = None, max_steps: int = 100) -> None:
        """Initialize flow executor."""
        self._scene_executor = scene_executor or SceneExecutor()
        self._max_steps = max_steps

    async def execute(self, ctx: InteractiveExecutionContext) -> str:
        """Run the configured flow to completion."""
        flow = ctx.script.flow
        current_state_id = flow.initial
        steps = 0
        result: str | None = None
        while steps < self._max_steps:
            steps += 1
            ctx.current_state_id = current_state_id
            state_spec = flow.states[current_state_id]
            self._apply_state_effects(ctx, state_spec.entry_effects)
            for scene_id in state_spec.scenes:
                forced_target = ctx.session_metadata.pop("interactive_next_target", None)
                if forced_target:
                    if forced_target in ctx.script.scenes:
                        scene_id = forced_target
                    elif forced_target in flow.states:
                        current_state_id = forced_target
                        break
                scene = ctx.script.scenes[scene_id]
                result = await self._scene_executor.execute(ctx, scene)
                if result:
                    ctx.ended = True
                    ctx.result = result
                    return result
            self._apply_state_effects(ctx, state_spec.exit_effects)
            if flow.type == "sequence" or state_spec.terminal:
                break
            next_state_id = self._next_state(ctx, state_spec)
            if next_state_id == current_state_id and not state_spec.transitions:
                break
            current_state_id = next_state_id
        if steps >= self._max_steps:
            result = "interactive_session_max_steps_reached"
        return result or "interactive_session_completed"

    def _next_state(
        self,
        ctx: InteractiveExecutionContext,
        state_spec: FlowStateSpec,
    ) -> str:
        """Resolve next state from transitions."""
        for transition in state_spec.transitions:
            if transition.when is None:
                return transition.to
            if ctx.condition_evaluator.evaluate(
                transition.when,
                ctx.state,
                actor=None,
                responses=ctx.last_responses,
                extra=ctx.runtime_extra(),
            ):
                return transition.to
        return state_spec.id

    def _apply_state_effects(self, ctx: InteractiveExecutionContext, effects: list[dict]) -> None:
        """Apply state entry/exit effects."""
        normalized = []
        for effect in effects or []:
            item = dict(effect)
            if item.get("type") == "set_state" and "path" in item:
                entity, attr = str(item.pop("path")).split(".", 1)
                item["entity"] = entity
                item["attr"] = attr
            normalized.append(item)
        if normalized:
            ctx.effect_executor.execute_all(
                normalized,
                ctx.state,
                ctx.writer,
                ctx.last_responses,
                actor=None,
                extra=ctx.runtime_extra(),
            )
