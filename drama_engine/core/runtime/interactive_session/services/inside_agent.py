"""Default ccserver Agent factory for `provider: inside` services."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class InsideAgentFactory:
    """Create and cache a hidden ccserver Agent for runtime services."""

    METADATA_KEY = "__interactive_inside_agent"

    def get_or_create(self, metadata: dict[str, Any], spec: dict[str, Any]) -> Any | None:
        """Return an injected or lazily-created inside Agent.

        Args:
            metadata: Session metadata owned by the interactive runtime.
            spec: DSL service/evaluator declaration.

        Returns:
            A ccserver Agent-like object with async `run()`, or `None` when the
            local Agent stack cannot be initialized.
        """
        assert isinstance(metadata, dict), "metadata 必须是 dict"
        existing = metadata.get(self.METADATA_KEY)
        if existing is not None:
            return existing
        if metadata.get("dry_run") is True or spec.get("dry_run") is True:
            return None
        try:
            agent = self._create_agent(metadata, spec)
        except Exception as exc:  # noqa: BLE001 - runtime falls back deterministically.
            metadata["__interactive_inside_agent_error"] = str(exc)
            return None
        metadata[self.METADATA_KEY] = agent
        return agent

    def _create_agent(self, metadata: dict[str, Any], spec: dict[str, Any]) -> Any:
        """Instantiate a ccserver Agent through the normal factory path."""
        from ccserver.emitters.collect import CollectEmitter
        from ccserver.factory import AgentFactory
        from ccserver.session import SessionManager

        project_root = spec.get("project_root") or metadata.get("project_root") or os.getcwd()
        session_manager = SessionManager(project_root=Path(str(project_root)))
        session = session_manager.create()
        emitter = CollectEmitter()
        system_prompt = str(
            spec.get("system")
            or spec.get("system_prompt")
            or "你是 interactive_session runtime 的内部裁判和剧情规划 Agent。"
        )
        kwargs: dict[str, Any] = {
            "name": str(spec.get("agent_id") or spec.get("name") or "interactive_inside_agent"),
            "system": system_prompt,
            "append_system": True,
            "run_mode": "auto",
            "stream": False,
        }
        if spec.get("model"):
            kwargs["model"] = str(spec["model"])
        return AgentFactory.create_root(session, emitter, **kwargs)
