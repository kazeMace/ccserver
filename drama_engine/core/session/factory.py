"""Session runtime factory helpers."""

from __future__ import annotations

from drama_engine.core.session.actions import ActionRequestService
from drama_engine.core.ports.timeout import TimeoutPolicy
from drama_engine.core.game_instance.state import SessionState

def _build_action_request_service(session: SessionState) -> ActionRequestService:
    """Create service action facade from session runtime config."""
    assert session is not None, "session 不能为空"
    timeout_policy = TimeoutPolicy.from_dict(dict(session.params.get("timeout_policy") or {}))
    return ActionRequestService(session.session_id, timeout_policy=timeout_policy)

__all__ = ["_build_action_request_service"]
