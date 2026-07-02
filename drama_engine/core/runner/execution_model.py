"""Execution model protocol for runner-specific game behavior.

Execution models contain the domain-specific work behind a session runner.
They let the project keep one lifecycle shell while fixed-flow games, group
chat, and dynamic story runtimes keep their own state and loop semantics.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ExecutionModel(Protocol):
    """Protocol implemented by concrete game execution models."""

    async def assign(self, context: Any) -> None:
        """Prepare domain state before a session starts."""

    async def start(self, context: Any) -> None:
        """Start domain execution."""

    async def pause(self, context: Any) -> None:
        """Pause domain execution if it has active work."""

    async def resume(self, context: Any) -> None:
        """Resume domain execution if it has active work."""

    async def step(self, context: Any, count: int = 1) -> None:
        """Advance the domain by one or more checkpoints."""

    async def terminate(self, context: Any, reason: str = "terminated") -> None:
        """Terminate domain execution and release transient state."""

    async def reset_runtime_state(self, context: Any) -> None:
        """Reset transient domain state for restart."""

    def summary(self, context: Any, audience: str, seat_id: str | None = None) -> dict[str, Any]:
        """Return a domain summary for one audience."""

    def current_action(self, context: Any, seat_id: str) -> dict[str, Any] | None:
        """Return the current action request for one seat, if any."""

    def action_port(self, context: Any) -> Any | None:
        """Return a domain-owned action port, if any."""
