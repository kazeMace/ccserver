"""Candidate validation for interactive participant responses."""

from __future__ import annotations

from typing import Any


class CandidateResponseValidator:
    """Validate structured response fields against resolved candidates."""

    def validate(
        self,
        response: dict[str, Any],
        candidates: list[str],
        scene_id: str,
    ) -> str:
        """Return error text when response selects an invalid candidate.

        Args:
            response: Actor response dictionary.
            candidates: Resolved candidate ids for the current actor.
            scene_id: Current scene id for readable errors.

        Returns:
            Empty string when valid; otherwise a human-readable error.
        """
        if not candidates:
            return ""
        data = response.get("data")
        if not isinstance(data, dict):
            return ""
        field_name = self._selected_field(data)
        if field_name is None:
            return ""
        selected = data.get(field_name)
        if field_name == "target" and data.get("action") is False:
            return ""
        if field_name == "targets":
            if not isinstance(selected, list):
                return f"{scene_id} 的字段 targets 必须是列表。"
            invalid = [item for item in selected if str(item) not in candidates]
            if invalid:
                return self._error(scene_id, field_name, invalid, candidates)
            return ""
        if selected is None:
            return ""
        if str(selected) not in candidates:
            return self._error(scene_id, field_name, [selected], candidates)
        return ""

    def _selected_field(self, data: dict[str, Any]) -> str | None:
        """Return the structured candidate field used by response data."""
        for field_name in ("vote", "choose", "target", "targets"):
            if field_name in data:
                return field_name
        return None

    def _error(
        self,
        scene_id: str,
        field_name: str,
        invalid: list[Any],
        candidates: list[str],
    ) -> str:
        """Build one readable validation error."""
        candidate_text = "、".join(str(name) for name in candidates)
        return (
            f"{scene_id} 的字段 {field_name} 包含非法候选 {invalid!r}；"
            f"可选候选为：{candidate_text}。"
        )
