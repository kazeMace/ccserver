"""Materialize base flow plus runtime patches."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from drama_engine.core.runtime.interactive_session.models import InteractiveScript
from drama_engine.core.runtime.interactive_session.patch.journal import PatchJournal


class FlowMaterializer:
    """Build an executable flow snapshot from base script and patches."""

    def materialize(
        self,
        script: InteractiveScript,
        journal: PatchJournal,
        base_raw: dict[str, Any] | None = None,
        extra_flow_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a serializable materialized-flow view."""
        assert script is not None, "script 不能为空"
        raw = deepcopy(base_raw or script.raw or {})
        scenes = deepcopy(raw.get("scenes") or {})
        flow = deepcopy(raw.get("flow") or {})
        for record in journal.by_type("flow_patch"):
            self._apply_flow_patch(flow, scenes, record.payload)
        if extra_flow_patch is not None:
            self._apply_flow_patch(flow, scenes, extra_flow_patch)
        return {"flow": flow, "scenes": scenes, "patches": journal.snapshot()}

    def materialize_raw(
        self,
        script: InteractiveScript,
        journal: PatchJournal,
        base_raw: dict[str, Any] | None = None,
        extra_flow_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a full raw script document from base flow and patches."""
        raw = deepcopy(base_raw or script.raw or {})
        materialized = self.materialize(script, journal, raw, extra_flow_patch)
        raw["flow"] = materialized["flow"]
        raw["scenes"] = materialized["scenes"]
        return raw

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
            from_state = str(patch.get("from"))
            to_state = str(patch.get("to"))
            if from_state not in states:
                raise ValueError(f"add_transition.from 不存在: {from_state}")
            if to_state not in states:
                raise ValueError(f"add_transition.to 不存在: {to_state}")
            state = states[from_state]
            state.setdefault("transitions", []).append({
                "to": to_state,
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
