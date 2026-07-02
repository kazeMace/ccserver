"""Group-chat execution loop."""

from __future__ import annotations

import asyncio
from typing import Any

from drama_engine.core.execution_models.group_chat.domain_runtime import GroupChatDomainRuntime

class GroupChatLoop:
    """Execute the group-chat round loop with a shared Cast."""

    def __init__(
        self,
        domain_runtime: GroupChatDomainRuntime,
        cast: Any,
        emit_public: Any,
        emit_views: Any,
    ) -> None:
        assert domain_runtime is not None, "domain_runtime 不能为空"
        assert cast is not None, "cast 不能为空"
        assert emit_public is not None, "emit_public 不能为空"
        assert emit_views is not None, "emit_views 不能为空"
        self.domain_runtime = domain_runtime
        self.cast = cast
        self.emit_public = emit_public
        self.emit_views = emit_views

    async def run(self) -> None:
        """Run every configured chat round and record transcript messages."""
        state = self.domain_runtime.state
        self.emit_public({
            "kind": "group_chat_room_opened",
            "room_name": state.room_name,
            "topic": state.topic,
            "participants": list(state.participants),
        })
        for round_index in range(1, state.max_rounds + 1):
            self.emit_public({
                "kind": "group_chat_round_started",
                "round": round_index,
                "phase": self.domain_runtime.policy.phase_for(round_index),
            })
            for participant in state.participants:
                message = await self.actor_message(
                    participant=participant,
                    round_index=round_index,
                )
                self.domain_runtime.record_message(message)
                self.emit_public(message)
                self.emit_views()
                await asyncio.sleep(0)

    async def actor_message(
        self,
        participant: str,
        round_index: int,
    ) -> dict[str, Any]:
        """Ask one actor for the next group-chat message."""
        state = self.domain_runtime.state
        actor = self.cast.get(participant)
        await actor.perceive(self.domain_runtime.policy.perception_for(state))
        cue = self.domain_runtime.policy.cue_for(participant, round_index, state.transcript)
        response = await actor.act(cue)
        text = str((response or {}).get("text") or "").strip()
        if not text:
            return self.domain_runtime.policy.fallback_message(participant, round_index, state.transcript)
        return {
            "kind": "group_chat_message",
            "round": round_index,
            "speaker": participant,
            "text": text,
            "source": "actor",
        }

__all__ = ["GroupChatLoop"]
