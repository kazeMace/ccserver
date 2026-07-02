"""Event publishing port."""

from __future__ import annotations

from typing import Any

class EventPublisher:
    """Thin event publishing facade over the service event store."""

    def __init__(self, event_store: Any) -> None:
        assert event_store is not None, "event_store 不能为空"
        self._event_store = event_store

    @property
    def sink(self) -> Any:
        """Return the underlying service event sink."""
        return self._event_store

    def public(self, event: dict[str, Any]) -> None:
        """Publish one public event."""
        self._event_store.append_public(dict(event))

    def host(self, event: dict[str, Any]) -> None:
        """Publish one host event."""
        self._event_store.append_host(dict(event))

    def private(self, seat_id: str, event: dict[str, Any]) -> None:
        """Publish one private event."""
        self._event_store.append_private(seat_id, dict(event))
