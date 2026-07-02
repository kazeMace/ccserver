"""Dynamic-story domain runtime slice."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from drama_engine.core.execution_models.dynamic_story.policy import DynamicStoryPolicy
from drama_engine.core.execution_models.dynamic_story.projector import DynamicStoryViewProjector
from drama_engine.core.execution_models.dynamic_story.state import (
    DynamicStoryState,
    WorldMemory,
    WorldStateWriter,
)

@dataclass(slots=True)
class DynamicStoryDomainRuntime:
    """Domain runtime slice for one dynamic-story runner."""

    state: DynamicStoryState
    world: WorldMemory
    policy: DynamicStoryPolicy
    memory_store: Any
    world_writer: WorldStateWriter = field(default_factory=WorldStateWriter)
    projector: DynamicStoryViewProjector = field(default_factory=DynamicStoryViewProjector)

    def remember(self, event: dict[str, Any], include_world: bool = False) -> None:
        """Record one story event in domain state and runtime memory."""
        if include_world:
            self.world_writer.remember_with_world(
                state=self.state,
                world=self.world,
                memory_store=self.memory_store,
                event=event,
            )
            return
        self.world_writer.remember_event(
            state=self.state,
            memory_store=self.memory_store,
            event=event,
        )

    def remember_world(self) -> None:
        """Record a world snapshot in runtime memory."""
        self.world_writer.remember_world(memory_store=self.memory_store, world=self.world)

    def project_views(self) -> list[dict[str, Any]]:
        """Return current ViewHost projections."""
        return self.projector.project(self.state, self.world)

__all__ = ["DynamicStoryDomainRuntime"]
