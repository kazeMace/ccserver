"""Patch validators for interactive_session."""

from __future__ import annotations

from typing import Any


class PatchValidator:
    """Validate flow and schedule patch shapes before execution."""

    def validate_schedule_patch(self, patch: dict[str, Any]) -> list[str]:
        """Validate a schedule patch."""
        errors: list[str] = []
        if not isinstance(patch, dict):
            return ["schedule_patch 必须是 dict"]
        if patch.get("__invalid_reason"):
            return [str(patch["__invalid_reason"])]
        patch_type = patch.get("type")
        if patch_type not in {"push_schedule", "pop_schedule"}:
            errors.append("schedule_patch.type 必须是 push_schedule 或 pop_schedule")
        if patch_type == "push_schedule":
            if not patch.get("mode"):
                errors.append("push_schedule 缺少 mode")
            elif patch.get("mode") not in {
                "none",
                "single",
                "sequential",
                "simultaneous",
                "random_order",
                "openchat",
                "loop_until",
            }:
                errors.append(f"未知 schedule_patch.mode: {patch.get('mode')}")
            participants = patch.get("participants")
            if not isinstance(participants, list) or not participants:
                errors.append("push_schedule.participants 必须是非空列表")
        return errors

    def validate_flow_patch(self, patch: dict[str, Any], script: Any | None = None) -> list[str]:
        """Validate a flow patch."""
        errors: list[str] = []
        if not isinstance(patch, dict):
            return ["flow_patch 必须是 dict"]
        patch_type = patch.get("type")
        if patch_type not in {"add_scene", "add_transition", "set_state"}:
            errors.append("flow_patch.type 必须是 add_scene/add_transition/set_state")
        if patch_type == "add_scene":
            scene = patch.get("scene")
            if not isinstance(scene, dict):
                errors.append("add_scene 需要 scene 字典")
            elif not (scene.get("id") or scene.get("name")):
                errors.append("add_scene.scene 需要 id 或 name")
            if script is not None and patch.get("state"):
                state_names = set(getattr(script.flow, "states", {}).keys())
                state_id = str(patch.get("state"))
                if state_id not in state_names:
                    errors.append(f"add_scene.state 不存在: {state_id}")
        if patch_type == "add_transition":
            if not patch.get("from") or not patch.get("to"):
                errors.append("add_transition 需要 from/to")
            elif script is not None:
                state_names = set(getattr(script.flow, "states", {}).keys())
                from_state = str(patch.get("from"))
                to_state = str(patch.get("to"))
                if from_state not in state_names:
                    errors.append(f"add_transition.from 不存在: {from_state}")
                if to_state not in state_names:
                    errors.append(f"add_transition.to 不存在: {to_state}")
        if patch_type == "set_state":
            path = patch.get("path")
            has_path = isinstance(path, str) and "." in path
            has_entity_attr = bool(patch.get("entity")) and bool(patch.get("attr"))
            if not has_path and not has_entity_attr:
                errors.append("set_state 需要 path 或 entity/attr")
        return errors
