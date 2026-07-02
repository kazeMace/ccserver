"""Runtime lifecycle state and hooks."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any

@dataclass(slots=True)
class RuntimeState:
    """Runtime-owned transient execution state."""

    phase: str = "idle"
    task: Any = None
    result: Any = None
    metadata: dict[str, Any] | None = None

class RuntimeLifecycleHooks:
    """Runtime lifecycle hook registry.

    Hooks receive ``runtime``, ``action`` and ``payload`` keyword arguments.
    They may be normal callables or async callables.
    """

    def __init__(self) -> None:
        self._callbacks: dict[str, list[Any]] = {}

    def register(self, event_name: str, callback: Any) -> None:
        """Register a callback for one lifecycle event."""
        assert event_name, "event_name 不能为空"
        assert callback is not None, "callback 不能为空"
        self._callbacks.setdefault(event_name, []).append(callback)

    async def emit(self, event_name: str, runtime: Any, action: str, payload: dict[str, Any] | None = None) -> None:
        """Emit one lifecycle event."""
        assert event_name, "event_name 不能为空"
        assert runtime is not None, "runtime 不能为空"
        assert action, "action 不能为空"
        event_payload = dict(payload or {})
        for callback in list(self._callbacks.get(event_name, [])):
            result = callback(runtime=runtime, action=action, payload=event_payload)
            if inspect.isawaitable(result):
                await result
