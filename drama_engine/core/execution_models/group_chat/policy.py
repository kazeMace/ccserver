"""Group-chat actor policy."""

from __future__ import annotations

from typing import Any

from drama_engine.core.execution_models.group_chat.state import GroupChatState

class GroupChatPolicy:
    """Build group-chat actor context, cues, and fallback messages.

    The production agent policy can replace this class later. Keeping it as a
    separate object makes the runner depend on a small abstraction rather than
    hard-coding message generation in the lifecycle code.
    """

    def __init__(
        self,
        topic: str,
        role_prompts: dict[str, str] | None = None,
        memory_store: Any = None,
        max_context_items: int = 3,
        discussion_phases: list[str] | None = None,
        room_rules: list[str] | None = None,
    ) -> None:
        self.topic = topic
        self.role_prompts = role_prompts or {}
        self.memory_store = memory_store
        self.max_context_items = max(0, int(max_context_items))
        self.discussion_phases = discussion_phases or ["提出观点", "回应他人", "收敛结论"]
        self.room_rules = room_rules or []

    def perception_for(self, state: GroupChatState) -> dict[str, Any]:
        """Build the perception event sent before a speaker acts."""
        long_term_context = self._long_term_context()
        memory_text = ""
        if long_term_context:
            memory_text = "；长期记忆：" + " / ".join(
                str(item.get("text") or item.get("summary") or item)
                for item in long_term_context
            )
        return {
            "scope": "group_chat",
            "sender": "system",
            "text": (
                f"房间：{state.room_name}；主题：{state.topic}；"
                f"参与者：{', '.join(state.participants)}；"
                f"当前摘要：{state.summary or '暂无'}"
                f"{memory_text}"
            ),
        }

    def cue_for(self, speaker: str, round_index: int, transcript: list[dict[str, Any]]) -> str:
        """Build the cue sent to the speaker actor."""
        prompt = self.role_prompts.get(speaker, "围绕主题提出一个具体观点")
        previous_count = len([item for item in transcript if item.get("speaker") == speaker])
        phase = self.phase_for(round_index)
        recent_text = self._recent_transcript_text(transcript)
        rule_text = ""
        if self.room_rules:
            rule_text = "；房间规则：" + "；".join(self.room_rules)
        return (
            f"你正在参与群聊房间。主题是「{self.topic}」。"
            f"现在是第 {round_index} 轮，讨论阶段：{phase}。"
            f"请以 {speaker} 的身份发言。"
            f"发言要求：{prompt}。你此前已发言 {previous_count} 次。"
            f"{recent_text}{rule_text}"
        )

    def phase_for(self, round_index: int) -> str:
        """Return the configured discussion phase for one round."""
        assert round_index > 0, "round_index 必须大于 0"
        if not self.discussion_phases:
            return "自由讨论"
        index = min(round_index - 1, len(self.discussion_phases) - 1)
        return self.discussion_phases[index]

    def fallback_message(self, speaker: str, round_index: int, transcript: list[dict[str, Any]]) -> dict[str, Any]:
        """Generate a deterministic fallback message."""
        prompt = self.role_prompts.get(speaker, "围绕主题提出一个具体观点")
        previous_count = len([item for item in transcript if item.get("speaker") == speaker])
        text = f"{speaker} 第 {round_index} 轮：{prompt}；主题是「{self.topic}」；此前发言 {previous_count} 次。"
        return {
            "kind": "group_chat_message",
            "round": round_index,
            "speaker": speaker,
            "text": text,
        }

    def _long_term_context(self) -> list[dict[str, Any]]:
        """Recall long-term memories for this topic."""
        if self.memory_store is None or not hasattr(self.memory_store, "recall_long_term"):
            return []
        return self.memory_store.recall_long_term(f"group_chat:{self.topic}", limit=self.max_context_items)

    def _recent_transcript_text(self, transcript: list[dict[str, Any]]) -> str:
        """Return compact recent transcript text for actor cues."""
        if self.max_context_items <= 0 or not transcript:
            return ""
        recent = transcript[-self.max_context_items:]
        text = " / ".join(
            f"{item.get('speaker')}: {item.get('text')}"
            for item in recent
        )
        return f"最近发言：{text}。"


GroupChatInterpreter = GroupChatPolicy

__all__ = ["GroupChatInterpreter", "GroupChatPolicy"]
