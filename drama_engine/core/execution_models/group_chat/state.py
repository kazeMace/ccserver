"""Group-chat state and transcript memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

@dataclass(slots=True)
class GroupChatState:
    """In-memory state for one group-chat session."""

    room_name: str
    topic: str
    participants: list[str]
    max_rounds: int
    transcript: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""


class TranscriptMemory:
    """Transcript memory with a compact persisted summary."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def append(self, message: dict[str, Any]) -> None:
        """Append one normalized message."""
        assert message.get("speaker"), "message.speaker 不能为空"
        assert message.get("text"), "message.text 不能为空"
        self.messages.append(dict(message))

    def summarize(self, limit: int = 6) -> str:
        """Return a short deterministic summary for metadata and views."""
        recent = self.messages[-limit:]
        return " / ".join(f"{item['speaker']}: {item['text']}" for item in recent)


class TranscriptWriter:
    """Write group-chat transcript state and runtime memory."""

    def record(
        self,
        state: GroupChatState,
        transcript_memory: TranscriptMemory,
        memory_store: Any,
        message: dict[str, Any],
    ) -> None:
        """Record one message in transcript state and memory buckets."""
        assert state is not None, "group chat state 不能为空"
        assert transcript_memory is not None, "transcript_memory 不能为空"
        assert memory_store is not None, "memory_store 不能为空"
        assert isinstance(message, dict), "message 必须是 dict"
        state.transcript.append(message)
        transcript_memory.append(message)
        memory_store.append("group_chat.transcript", message)
        if hasattr(memory_store, "remember_long_term"):
            memory_store.remember_long_term(
                f"group_chat:{state.topic}",
                {
                    "kind": "group_chat_message",
                    "room_name": state.room_name,
                    "topic": state.topic,
                    "speaker": message.get("speaker"),
                    "text": message.get("text"),
                    "round": message.get("round"),
                },
            )
        state.summary = transcript_memory.summarize()
        memory_store.append("group_chat.summary", {
            "kind": "group_chat_summary",
            "round": message.get("round"),
            "summary": state.summary,
        })

__all__ = ["GroupChatState", "TranscriptMemory", "TranscriptWriter"]
