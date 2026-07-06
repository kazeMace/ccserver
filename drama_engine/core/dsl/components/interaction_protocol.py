"""Shared interaction protocol envelope for runtime service calls."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class InteractionProtocolBuilder:
    """Build versioned request envelopes for internal/external services."""

    PROTOCOL_NAME = "interactive_session"
    PROTOCOL_VERSION = "1.0"
    SCHEMA = "interactive_session.v1"

    def build(
        self,
        *,
        runtime_type: str,
        purpose: str,
        provider: str,
        input_payload: dict[str, Any],
        context_payload: dict[str, Any],
        name: str | None = None,
        call_id: str | None = None,
        endpoint: str | None = None,
        hook: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a stable protocol envelope.

        Args:
            runtime_type: Runtime family, normally ``interactive_session``.
            purpose: Semantic call purpose, such as ``openchat_planner``.
            provider: Provider family, such as ``plugin``, ``http`` or ``inside``.
            input_payload: Materialized input requested by the DSL ``input`` spec.
            context_payload: Full serializable runtime context.
            name: Optional service/evaluator name.
            call_id: Optional stable call id from DSL.
            endpoint: Optional external endpoint name or URL.
            hook: Optional lifecycle hook that caused this call.
            metadata: Additional serializable call metadata.

        Returns:
            Dict with ``protocol``, ``call``, ``input``, ``context`` and
            ``metadata`` keys.  Callers may add legacy aliases around it.
        """
        assert runtime_type, "runtime_type 不能为空"
        assert purpose, "purpose 不能为空"
        assert provider, "provider 不能为空"
        return {
            "protocol": {
                "name": self.PROTOCOL_NAME,
                "version": self.PROTOCOL_VERSION,
                "schema": self.SCHEMA,
            },
            "call": {
                "id": call_id,
                "name": name,
                "purpose": purpose,
                "provider": provider,
                "endpoint": endpoint,
                "hook": hook,
                "runtime_type": runtime_type,
            },
            "input": deepcopy(input_payload or {}),
            "context": deepcopy(context_payload or {}),
            "metadata": deepcopy(metadata or {}),
        }

    def with_legacy_aliases(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """Return an envelope plus historical top-level aliases."""
        call = envelope.get("call") or {}
        result = deepcopy(envelope)
        result["id"] = call.get("id")
        result["name"] = call.get("name")
        result["purpose"] = call.get("purpose")
        result["endpoint"] = call.get("endpoint")
        result["input"] = deepcopy(envelope.get("input") or {})
        result["context"] = deepcopy(envelope.get("context") or {})
        return result
