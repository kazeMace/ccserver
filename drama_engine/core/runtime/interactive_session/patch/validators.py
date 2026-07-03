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
            participants = patch.get("participants")
            if not isinstance(participants, list) or not participants:
                errors.append("push_schedule.participants 必须是非空列表")
        return errors

    def validate_flow_patch(self, patch: dict[str, Any]) -> list[str]:
        """Validate a flow patch."""
        errors: list[str] = []
        if not isinstance(patch, dict):
            return ["flow_patch 必须是 dict"]
        patch_type = patch.get("type")
        if patch_type not in {"add_scene", "add_transition", "set_state"}:
            errors.append("flow_patch.type 必须是 add_scene/add_transition/set_state")
        if patch_type == "add_scene" and not isinstance(patch.get("scene"), dict):
            errors.append("add_scene 需要 scene 字典")
        if patch_type == "add_transition":
            if not patch.get("from") or not patch.get("to"):
                errors.append("add_transition 需要 from/to")
        return errors
