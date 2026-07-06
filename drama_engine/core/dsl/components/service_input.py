"""Build input payloads for evaluator and runtime-service calls.

This helper keeps the DSL `input:` contract in one place.  Callers provide a
full default payload and an optional resolver for `{ref: ...}` expressions; the
builder returns the exact payload requested by include flags or explicit fields.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable


Resolver = Callable[[Any], Any]


class ServiceInputBuilder:
    """Materialize `input:` declarations for external evaluators/services."""

    INCLUDE_KEYS = {
        "include_state",
        "include_players",
        "include_participants",
        "include_messages",
        "include_recent_messages",
        "include_message",
        "include_story_summary",
        "include_responses",
        "include_patch_journal",
        "include_metadata",
    }

    def build(
        self,
        input_spec: Any,
        default_payload: dict[str, Any],
        resolver: Resolver | None = None,
    ) -> Any:
        """Return the service input requested by a DSL input spec.

        Args:
            input_spec: Value from DSL `input:`.  `None` means use the complete
                default payload.  Dicts may contain include flags and explicit
                fields.
            default_payload: Complete serializable runtime/evaluator context.
            resolver: Optional callback used to resolve `{ref: ...}` objects.

        Returns:
            Any JSON-friendly payload for the provider call.
        """
        assert isinstance(default_payload, dict), "default_payload 必须是 dict"
        if input_spec is None:
            return deepcopy(default_payload)
        return self._resolve(input_spec, default_payload, resolver)

    def _resolve(
        self,
        value: Any,
        default_payload: dict[str, Any],
        resolver: Resolver | None,
    ) -> Any:
        """Resolve nested input values."""
        if isinstance(value, dict):
            if set(value.keys()) == {"ref"} and resolver is not None:
                return resolver(value)
            if self._has_include_flags(value):
                return self._build_from_flags(value, default_payload, resolver)
            return {
                str(key): self._resolve(item, default_payload, resolver)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                self._resolve(item, default_payload, resolver)
                for item in value
            ]
        return deepcopy(value)

    def _has_include_flags(self, spec: dict[str, Any]) -> bool:
        """Return whether a dict contains include flags."""
        return any(key in spec for key in self.INCLUDE_KEYS)

    def _build_from_flags(
        self,
        spec: dict[str, Any],
        default_payload: dict[str, Any],
        resolver: Resolver | None,
    ) -> dict[str, Any]:
        """Build a compact input object from include flags."""
        result: dict[str, Any] = {}
        if spec.get("include_state"):
            result["state"] = deepcopy(default_payload.get("state", {}))
        if spec.get("include_players"):
            result["players"] = deepcopy(default_payload.get("players", []))
        if spec.get("include_participants"):
            result["participants"] = deepcopy(
                default_payload.get("participants", default_payload.get("players", []))
            )
        if spec.get("include_messages"):
            result["messages"] = self._messages(default_payload)
        if spec.get("include_recent_messages"):
            limit = int(spec.get("recent_limit") or spec.get("message_limit") or 10)
            result["recent_messages"] = self._messages(default_payload)[-max(0, limit):]
        if spec.get("include_message"):
            result["message"] = self._last_message(default_payload)
        if spec.get("include_story_summary"):
            result["story_summary"] = self._story_summary(default_payload)
        if spec.get("include_responses"):
            result["responses"] = deepcopy(
                default_payload.get("responses", default_payload.get("last_responses", []))
            )
        if spec.get("include_patch_journal"):
            result["patch_journal"] = deepcopy(
                default_payload.get("patch_journal", default_payload.get("patches", []))
            )
        if spec.get("include_metadata"):
            result["metadata"] = deepcopy(default_payload.get("metadata", {}))

        for key, value in spec.items():
            if key in self.INCLUDE_KEYS or key in {"recent_limit", "message_limit"}:
                continue
            result[str(key)] = self._resolve(value, default_payload, resolver)
        return result

    def _messages(self, payload: dict[str, Any]) -> list[Any]:
        """Return the best available message/response list."""
        for key in ("messages", "last_responses", "responses"):
            value = payload.get(key)
            if isinstance(value, list):
                return deepcopy(value)
        return []

    def _last_message(self, payload: dict[str, Any]) -> Any:
        """Return one source message if present."""
        for key in ("source_response", "last_response", "message"):
            value = payload.get(key)
            if value is not None:
                return deepcopy(value)
        messages = self._messages(payload)
        return messages[-1] if messages else None

    def _story_summary(self, payload: dict[str, Any]) -> Any:
        """Return STORY state or explicit story summary when available."""
        if "story_summary" in payload:
            return deepcopy(payload["story_summary"])
        state = payload.get("state")
        if isinstance(state, dict):
            return deepcopy(state.get("STORY", {}))
        return {}
