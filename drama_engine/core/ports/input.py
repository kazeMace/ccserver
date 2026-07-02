"""Input bridge port."""

from __future__ import annotations

from typing import Any

class InputBridge:
    """Bridge runners to human, AI, and mock actor input creation."""

    def create_cast(
        self,
        actor_runtime: Any,
        player_names: list[str],
        human_seat_ids: set[str],
        action_service: RuntimeActionServiceRouter,
        tracer: Any = None,
        dry_run: bool = True,
        adapter_resolver: Any = None,
        step_gate: Any = None,
    ) -> Any:
        """Create a Cast through ActorRuntime."""
        assert actor_runtime is not None, "actor_runtime 不能为空"
        return actor_runtime.create_cast(
            player_names=player_names,
            human_seat_ids=human_seat_ids,
            action_service=action_service,
            tracer=tracer,
            dry_run=dry_run,
            adapter_resolver=adapter_resolver,
            step_gate=step_gate,
        )
