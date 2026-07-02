"""Unified session runner shell.

SessionRunner owns lifecycle delegation. It intentionally does not know the
rules of social deduction, group chat, or dynamic story games; those differences
belong to an ExecutionModel.
"""

from __future__ import annotations

from typing import Any

from drama_engine.core.runner.execution_model import ExecutionModel


class SessionRunner:
    """Lifecycle wrapper around one execution model."""

    def __init__(self, context: Any, model: ExecutionModel) -> None:
        """Create a runner shell for one model."""
        assert context is not None, "context 不能为空"
        assert isinstance(model, ExecutionModel), "model 必须实现 ExecutionModel"
        self.context = context
        self.model = model

    async def assign(self) -> None:
        """Prepare the session before start."""
        await self.model.assign(self.context)

    async def start(self) -> None:
        """Start session execution."""
        await self.model.start(self.context)

    async def pause(self) -> None:
        """Pause session execution."""
        await self.model.pause(self.context)

    async def resume(self) -> None:
        """Resume session execution."""
        await self.model.resume(self.context)

    async def step(self, count: int = 1) -> None:
        """Advance one or more execution checkpoints."""
        assert count >= 0, "count 不能为负数"
        await self.model.step(self.context, count=count)

    async def reset_runtime_state(self) -> None:
        """Reset transient execution state."""
        await self.model.reset_runtime_state(self.context)

    async def terminate(self, reason: str = "terminated") -> None:
        """Terminate execution."""
        await self.model.terminate(self.context, reason=reason)

    def summary(self, audience: str, seat_id: str | None = None) -> dict[str, Any]:
        """Return a summary for one audience."""
        return self.model.summary(self.context, audience=audience, seat_id=seat_id)

    def current_action(self, seat_id: str) -> dict[str, Any] | None:
        """Return the current action for one seat, if any."""
        return self.model.current_action(self.context, seat_id=seat_id)

    def action_port(self) -> Any | None:
        """Return the model action port, if any."""
        return self.model.action_port(self.context)
