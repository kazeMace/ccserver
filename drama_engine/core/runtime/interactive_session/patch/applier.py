"""Apply flow patches to the live interactive_session script."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from drama_engine.core.engine import SetAttr
from drama_engine.core.runtime.interactive_session.compiler import InteractiveSessionCompiler
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext


class FlowPatchApplier:
    """Apply validated flow patches to the in-memory script snapshot."""

    def __init__(self) -> None:
        """Initialize the applier."""
        self._compiler = InteractiveSessionCompiler()

    def apply(self, ctx: InteractiveExecutionContext, patch: dict[str, Any]) -> None:
        """Apply one flow patch to runtime memory.

        Args:
            ctx: Runtime execution context.
            patch: Validated flow_patch dictionary.

        Raises:
            ValueError: When patch type is unsupported.
        """
        patch_type = str(patch.get("type") or "")
        if patch_type == "add_scene":
            self._apply_add_scene(ctx, patch)
            return
        if patch_type == "add_transition":
            self._apply_add_transition(ctx, patch)
            return
        if patch_type == "set_state":
            self._apply_set_state(ctx, patch)
            return
        raise ValueError(f"未知 flow_patch.type: {patch_type}")

    def _apply_add_scene(self, ctx: InteractiveExecutionContext, patch: dict[str, Any]) -> None:
        """Add a generated scene and insert it into the active flow."""
        scene = deepcopy(patch.get("scene") or {})
        scene_id = str(scene.get("id") or scene.get("name") or "")
        if not scene_id:
            raise ValueError("add_scene.scene.id 不能为空")
        raw = deepcopy(ctx.script.raw)
        raw.setdefault("scenes", {})[scene_id] = scene
        flow = raw.setdefault("flow", {})
        flow_type = str(flow.get("type") or ctx.script.flow.type or "sequence")
        if flow_type == "sequence":
            scenes = flow.setdefault("scenes", [])
            self._insert_scene_id(scenes, scene_id, str(patch.get("after") or ctx.current_scene_id))
        else:
            state_id = str(patch.get("state") or ctx.current_state_id or flow.get("initial") or "")
            states = flow.setdefault("states", {})
            state = states.setdefault(state_id, {"scenes": [], "transitions": []})
            scenes = state.setdefault("scenes", [])
            self._insert_scene_id(scenes, scene_id, str(patch.get("after") or ctx.current_scene_id))
        self._replace_script(ctx, raw)

    def _apply_add_transition(self, ctx: InteractiveExecutionContext, patch: dict[str, Any]) -> None:
        """Add a state-machine transition."""
        raw = deepcopy(ctx.script.raw)
        flow = raw.setdefault("flow", {})
        flow.setdefault("type", "state_machine")
        states = flow.setdefault("states", {})
        from_state = str(patch.get("from") or ctx.current_state_id or flow.get("initial") or "")
        if not from_state:
            raise ValueError("add_transition.from 不能为空")
        state = states.setdefault(from_state, {"scenes": [], "transitions": []})
        state.setdefault("transitions", []).append({
            "to": patch.get("to"),
            "when": patch.get("when"),
        })
        self._replace_script(ctx, raw)

    def _apply_set_state(self, ctx: InteractiveExecutionContext, patch: dict[str, Any]) -> None:
        """Apply a state change flow patch."""
        path = patch.get("path")
        if not path and patch.get("entity") and patch.get("attr"):
            path = str(patch["entity"]) + "." + str(patch["attr"])
        if not path or "." not in str(path):
            raise ValueError("set_state patch 需要 path 或 entity/attr")
        entity, attr = str(path).split(".", 1)
        if not ctx.state.has_entity(entity):
            ctx.state.register_entity(entity, {})
        value = ctx.value_resolver.resolve(
            patch.get("value"),
            state=ctx.state,
            responses=ctx.last_responses,
            extra=ctx.runtime_extra(),
        )
        ctx.writer.apply(SetAttr(entity, attr, value))

    def _insert_scene_id(self, scenes: list[Any], scene_id: str, after: str) -> None:
        """Insert scene id after a known scene, otherwise append."""
        if scene_id in scenes:
            return
        if after and after in scenes:
            scenes.insert(scenes.index(after) + 1, scene_id)
            return
        scenes.append(scene_id)

    def _replace_script(self, ctx: InteractiveExecutionContext, raw: dict[str, Any]) -> None:
        """Compile and replace the mutable runtime script."""
        compiled = self._compiler.compile_doc(raw)
        ctx.script.flow = compiled.flow
        ctx.script.scenes = compiled.scenes
        ctx.script.scopes = compiled.scopes
        ctx.script.referee = compiled.referee
        ctx.script.raw = compiled.raw
