"""Service ports exposed to a party session runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

@dataclass(slots=True)
class ServicePorts:
    """Service-layer ports exposed to a runtime session.

    Runtime code uses this object when it needs service-owned session state,
    event sinks, or API action views.
    """

    session_state: Any
    event_sink: Any
    action_view: Any
    token_service: Any = None
    persistence: Any = None
