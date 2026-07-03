"""Materialize base flow plus runtime patches."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from drama_engine.core.runtime.interactive_session.models import InteractiveScript
from drama_engine.core.runtime.interactive_session.patch.journal import PatchJournal


class FlowMaterializer:
    """Build an executable flow snapshot from base script and patches."""

    def materialize(self, script: InteractiveScript, journal: PatchJournal) -> dict[str, Any]:
        """Return a serializable materialized-flow view."""
        assert script is not None, "script 不能为空"
        scenes = {scene_id: deepcopy(scene.raw) for scene_id, scene in script.scenes.items()}
        flow = deepcopy(script.raw.get("flow") or {})
        for record in journal.by_type("flow_patch"):
            self._apply_flow_patch(flow, scenes, record.payload)
        return {"flow": flow, "scenes": scenes, "patches": journal.snapshot()}

    def _apply_flow_patch(
        self,
        flow: dict[str, Any],
        scenes: dict[str, dict[str, Any]],
        patch: dict[str, Any],
    ) -> None:
        """Apply one patch to the serializable snapshot."""
        patch_type = patch.get("type")
        if patch_type == "add_scene":
            scene = dict(patch.get("scene") or {})
            scene_id = str(scene.get("id") or scene.get("name") or "")
            if scene_id:
                scenes[scene_id] = scene
                self._insert_scene(flow, scene_id, str(patch.get("after") or ""), str(patch.get("state") or ""))
        elif patch_type == "add_transition":
            states = flow.setdefault("states", {})
            state = states.setdefault(str(patch.get("from")), {"scenes": [], "transitions": []})
            state.setdefault("transitions", []).append({
                "to": patch.get("to"),
                "when": patch.get("when"),
            })

    def _insert_scene(
        self,
        flow: dict[str, Any],
        scene_id: str,
        after: str,
        state_id: str,
    ) -> None:
        """Insert generated scene id into materialized flow."""
        flow_type = str(flow.get("type") or "sequence")
        if flow_type == "sequence":
            scenes = flow.setdefault("scenes", [])
            self._insert_after(scenes, scene_id, after)
            return
        states = flow.setdefault("states", {})
        state_key = state_id or str(flow.get("initial") or next(iter(states), "main"))
        state = states.setdefault(state_key, {"scenes": [], "transitions": []})
        self._insert_after(state.setdefault("scenes", []), scene_id, after)

    def _insert_after(self, items: list[Any], value: str, after: str) -> None:
        """Insert value after another list item, or append."""
        if value in items:
            return
        if after and after in items:
            items.insert(items.index(after) + 1, value)
            return
        items.append(value)
