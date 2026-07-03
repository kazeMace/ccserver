"""Scope resolution for interactive_session."""

from __future__ import annotations

from typing import Any

from drama_engine.core.runtime.interactive_session.models import ScopeSpec


class ScopeResolver:
    """Resolve scope members from scope specs and current participants."""

    def members(
        self,
        scope: ScopeSpec | dict[str, Any],
        all_names: list[str],
        participants: list[str] | None = None,
    ) -> list[str]:
        """Return actor names allowed to see messages in a scope."""
        scope_spec = self._coerce(scope)
        if scope_spec.visibility == "private":
            members = scope_spec.members or participants or []
            return [name for name in members if name in all_names]
        if scope_spec.members:
            return [name for name in scope_spec.members if name in all_names]
        return list(all_names)

    def _coerce(self, scope: ScopeSpec | dict[str, Any]) -> ScopeSpec:
        """Coerce dict scope to ScopeSpec."""
        if isinstance(scope, ScopeSpec):
            return scope
        return ScopeSpec(
            id=str(scope.get("id") or "public"),
            visibility=str(scope.get("visibility") or "public"),
            members=[str(item) for item in scope.get("members", []) or []],
        )
