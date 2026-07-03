"""Schedule mode helpers."""

from __future__ import annotations

import random
from typing import Any

from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.models import ScheduleSpec


class ScheduleModePlanner:
    """Resolve actor order for each schedule mode."""

    def order(
        self,
        ctx: InteractiveExecutionContext,
        participants: list[str],
        schedule: ScheduleSpec,
    ) -> list[str]:
        """Return actor names for one schedule pass."""
        if schedule.mode == "none":
            return []
        if schedule.mode == "single":
            actor = self._resolve_single_actor(ctx, participants, schedule.actor)
            return [actor] if actor else []
        if schedule.mode == "random_order":
            result = list(participants)
            random.shuffle(result)
            return result
        return list(participants)

    def rounds(self, schedule: ScheduleSpec) -> int:
        """Return number of schedule rounds."""
        if schedule.mode == "openchat":
            return max(1, schedule.max_turns)
        if schedule.mode == "loop_until":
            return max(1, schedule.max_rounds)
        return 1

    def _resolve_single_actor(
        self,
        ctx: InteractiveExecutionContext,
        participants: list[str],
        actor_spec: Any,
    ) -> str | None:
        """Resolve actor for single mode."""
        if isinstance(actor_spec, str) and actor_spec in participants:
            return actor_spec
        if isinstance(actor_spec, dict):
            value = ctx.value_resolver.resolve(
                actor_spec,
                state=ctx.state,
                responses=ctx.last_responses,
                extra=ctx.runtime_extra(),
            )
            if value in participants:
                return str(value)
        return participants[0] if participants else None
