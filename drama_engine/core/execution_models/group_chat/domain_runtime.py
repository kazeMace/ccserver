"""Group-chat domain runtime slice."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from drama_engine.core.execution_models.group_chat.policy import GroupChatPolicy
from drama_engine.core.execution_models.group_chat.projector import GroupChatViewProjector
from drama_engine.core.execution_models.group_chat.state import (
    GroupChatState,
    TranscriptMemory,
    TranscriptWriter,
)

@dataclass(slots=True)
class GroupChatDomainRuntime:
    """Domain runtime slice for one group-chat runner."""

    state: GroupChatState
    policy: GroupChatPolicy
    memory_store: Any
    transcript_memory: TranscriptMemory = field(default_factory=TranscriptMemory)
    transcript_writer: TranscriptWriter = field(default_factory=TranscriptWriter)
    projector: GroupChatViewProjector = field(default_factory=GroupChatViewProjector)

    def record_message(self, message: dict[str, Any]) -> None:
        """Record one message in state, transcript memory, and runtime memory."""
        self.transcript_writer.record(
            state=self.state,
            transcript_memory=self.transcript_memory,
            memory_store=self.memory_store,
            message=message,
        )

    def project_views(self) -> list[dict[str, Any]]:
        """Return current ViewHost projections."""
        return self.projector.project(self.state)

__all__ = ["GroupChatDomainRuntime"]
