"""Dynamic-story state and world memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

@dataclass(slots=True)
class DynamicStoryState:
    """In-memory state for one dynamic-story session."""

    world_name: str
    premise: str
    players: list[str]
    beats: list[str]
    memory: list[dict[str, Any]] = field(default_factory=list)
    world_state: dict[str, Any] = field(default_factory=dict)


class WorldMemory:
    """Persistent world memory for dynamic story sessions."""

    def __init__(self, initial_state: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = dict(initial_state or {})
        self.events: list[dict[str, Any]] = []

    def remember(self, event: dict[str, Any]) -> None:
        """Store one story event and update compact counters."""
        self.events.append(dict(event))
        self.state["event_count"] = len(self.events)
        if event.get("location"):
            self.state["last_location"] = event["location"]
        if event.get("consequence"):
            self.state["last_consequence"] = event["consequence"]

    def snapshot(self) -> dict[str, Any]:
        """Return serializable world memory."""
        return {"state": dict(self.state), "events": list(self.events)}

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Restore world memory from a snapshot."""
        assert isinstance(snapshot, dict), "world snapshot 必须是 dict"
        self.state = dict(snapshot.get("state") or {})
        self.events = [dict(event) for event in (snapshot.get("events") or [])]


class WorldStateWriter:
    """Write dynamic-story memory and world snapshots."""

    def remember_event(
        self,
        state: DynamicStoryState,
        memory_store: Any,
        event: dict[str, Any],
    ) -> None:
        """Record one story event in domain state and runtime memory."""
        assert state is not None, "dynamic story state 不能为空"
        assert memory_store is not None, "memory_store 不能为空"
        assert isinstance(event, dict), "event 必须是 dict"
        state.memory.append(event)
        memory_store.append("dynamic_story.memory", event)
        if hasattr(memory_store, "remember_long_term"):
            memory_store.remember_long_term(
                f"dynamic_story:{state.world_name}",
                {
                    "kind": event.get("kind", "dynamic_story_memory"),
                    "world_name": state.world_name,
                    "premise": state.premise,
                    "text": event.get("text") or event.get("consequence") or "",
                    "actor": event.get("actor"),
                    "intent": event.get("intent"),
                    "index": event.get("index"),
                },
            )

    def remember_world(self, memory_store: Any, world: WorldMemory) -> None:
        """Record one world snapshot in runtime memory."""
        assert memory_store is not None, "memory_store 不能为空"
        assert world is not None, "world 不能为空"
        memory_store.append("dynamic_story.world", world.snapshot())

    def remember_with_world(
        self,
        state: DynamicStoryState,
        world: WorldMemory,
        memory_store: Any,
        event: dict[str, Any],
    ) -> None:
        """Record a story event and apply it to world memory."""
        self.remember_event(state=state, memory_store=memory_store, event=event)
        world.remember(event)
        self.remember_world(memory_store=memory_store, world=world)

__all__ = ["DynamicStoryState", "WorldMemory", "WorldStateWriter"]
