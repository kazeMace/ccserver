"""Flow executor for interactive_session."""

from __future__ import annotations

from drama_engine.core.dsl.components.value_resolver import parse_state_path
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
            state_spec = ctx.script.flow.states[current_state_id]
            self._apply_state_effects(ctx, state_spec.entry_effects)
            index = 0
            jump_state = None
            while index < len(ctx.script.flow.states[current_state_id].scenes):
                scene_ids = ctx.script.flow.states[current_state_id].scenes
                scene_id = scene_ids[index]
                index += 1
                result = await self._execute_scene_id(ctx, scene_id)
                if result:
                    ctx.ended = True
                    ctx.result = result
                    return result
                drain_result = await self._drain_targets(ctx, current_state_id, index)
                result = drain_result["result"]
                index = drain_result["index"]
                jump_state = drain_result["jump_state"]
                if result:
                    ctx.ended = True
                    ctx.result = result
                    return result
                if jump_state:
                    break
            self._apply_state_effects(ctx, state_spec.exit_effects)
            flow = ctx.script.flow
            state_spec = flow.states[current_state_id]
            if jump_state:
                current_state_id = jump_state
                continue
            if flow.type == "sequence" or state_spec.terminal:
                break
            next_state_id = await self._next_state(ctx, state_spec)
            if next_state_id is None:
                ctx.emit_host({
                    "kind": "interactive_session_flow_stopped",
                    "state": current_state_id,
                    "message": "state_machine 没有命中的 transition，流程在当前 state 后停止",
                })
                break
            current_state_id = next_state_id
        if steps >= self._max_steps:
            result = "interactive_session_max_steps_reached"
        return result or "interactive_session_completed"

    async def _execute_scene_id(self, ctx: InteractiveExecutionContext, scene_id: str) -> str | None:
        """Execute one scene by id."""
        scene = ctx.script.scenes[scene_id]
        return await self._scene_executor.execute(ctx, scene)

    async def _drain_targets(
        self,
        ctx: InteractiveExecutionContext,
        current_state_id: str,
        index: int,
    ) -> dict:
        """Consume chained scene/state targets and return stack entries."""
        result = None
        jump_state = None
        guard = 0
        forced_target = self._pop_next_target(ctx)
        while forced_target and guard < self._max_steps:
            guard += 1
            if forced_target in ctx.script.scenes:
                result = await self._execute_scene_id(ctx, forced_target)
                if result:
                    break
                if forced_target in ctx.script.flow.states[current_state_id].scenes:
                    index = ctx.script.flow.states[current_state_id].scenes.index(forced_target) + 1
                forced_target = self._pop_next_target(ctx)
                continue
            if forced_target in ctx.script.flow.states:
                jump_state = forced_target
                break
            ctx.emit_host({
                "kind": "interactive_session_warning",
                "message": f"未知 flow target: {forced_target}",
                "scene": ctx.current_scene_id,
            })
            forced_target = self._pop_next_target(ctx)
        if guard >= self._max_steps:
            result = "interactive_session_max_steps_reached"
        return {"result": result, "index": index, "jump_state": jump_state}

    def _pop_next_target(self, ctx: InteractiveExecutionContext) -> str | None:
        """Pop explicit target or return-stack target."""
        target = ctx.session_metadata.pop("interactive_next_target", None)
        if target:
            return str(target)
        stack = ctx.session_metadata.get("interactive_return_stack") or []
        if not stack:
            return None
        item = stack.pop()
        if isinstance(item, dict):
            return str(item.get("id") or item.get("scene") or item.get("state") or "")
        return str(item)

    async def _next_state(
        self,
        ctx: InteractiveExecutionContext,
        state_spec: FlowStateSpec,
    ) -> str | None:
        """Resolve next state from transitions."""
        for transition in state_spec.transitions:
            if transition.when is None:
                return transition.to
            if await ctx.condition_evaluator.evaluate_async(
                transition.when,
                ctx.state,
                actor=None,
                responses=ctx.last_responses,
                extra=ctx.condition_extra(),
            ):
                return transition.to
        return None

    def _apply_state_effects(self, ctx: InteractiveExecutionContext, effects: list[dict]) -> None:
        """Apply state entry/exit effects."""
        normalized = []
        for effect in effects or []:
            item = dict(effect)
            if item.get("type") == "set_state" and "path" in item:
                entity, attr = parse_state_path(str(item.pop("path")))
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
