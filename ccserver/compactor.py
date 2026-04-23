"""
compactor — conversation history compression.

Three-level strategy:
  1. micro()         — truncate old tool_result payloads in-place (free, no LLM)
  2. needs_compact() — cheap token estimate to decide when to act
  3. compact()       — LLM-based summarisation; archives full transcript to disk
"""

import json

from loguru import logger

from .config import MODEL, THRESHOLD, KEEP_RECENT
from .utils import estimate_tokens, get_block_attr
from .model import ModelAdapter


# ─── Compactor ────────────────────────────────────────────────────────────────


class Compactor:

    def __init__(
        self,
        adapter: ModelAdapter,
        model: str = MODEL,
        threshold: int = THRESHOLD,
        keep_recent: int = KEEP_RECENT,
    ):
        self.adapter = adapter
        self.model = model
        self.threshold = threshold
        self.keep_recent = keep_recent

    def needs_compact(self, messages: list) -> bool:
        """Return True when the message list exceeds the token threshold."""
        return estimate_tokens(messages) > self.threshold

    def micro(self, messages: list) -> list:
        """
        Truncate old tool_result payloads in-place to save tokens.
        Keeps the keep_recent most recent tool results at full length;
        replaces earlier ones with a short label.
        No LLM call — runs before every agent loop iteration.
        """
        tool_results = []
        for msg in messages:
            if msg["role"] == "user" and isinstance(msg.get("content"), list):
                for part in msg["content"]:
                    if isinstance(part, dict) and part.get("type") == "tool_result":
                        tool_results.append(part)

        if len(tool_results) <= self.keep_recent:
            return messages

        tool_name_map: dict[str, str] = {}
        for msg in messages:
            if msg["role"] == "assistant":
                for block in msg.get("content", []) if isinstance(msg.get("content"), list) else []:
                    if get_block_attr(block, "type") == "tool_use":
                        bid = get_block_attr(block, "id")
                        bname = get_block_attr(block, "name")
                        if bid and bname:
                            tool_name_map[bid] = bname

        truncated = 0
        for result in tool_results[:-self.keep_recent]:
            if isinstance(result.get("content"), str) and len(result["content"]) > 100:
                tid = result.get("tool_use_id", "")
                result["content"] = f"[Previous: used {tool_name_map.get(tid, 'unknown')}]"
                truncated += 1

        if truncated:
            logger.debug("micro_compact: truncated {} old tool results", truncated)
        return messages

    async def compact(
        self,
        session,
        emitter,
        messages: list = None,
        lib=None,           # PromptLib 实例，决定压缩消息格式；为 None 时使用默认格式
    ) -> list:
        """
        LLM-based compaction: summarise messages, archive originals, return compressed list.

        Archives the full transcript to disk before summarising so no history is lost.
        Returns a two-message list: [summary-as-user, ack-as-assistant].
        """
        msgs = messages if messages is not None else session.messages
        logger.info("auto_compact start | session={} messages={} tokens~{}", session.id[:8], len(msgs), estimate_tokens(msgs))
        transcript_ref = session.save_transcript(msgs)
        logger.debug("Transcript archived | ref={}", transcript_ref)
        await emitter.emit_compact(f"saved transcript: {transcript_ref}")

        conversation_text = json.dumps(msgs, default=str)[:80000]
        response = await self.adapter.create(
            model=self.model,
            messages=[{
                "role": "user",
                "content": (
                    "Summarize this conversation for continuity. Include: "
                    "1) What was accomplished, 2) Current state, 3) Key decisions. "
                    "Be concise but preserve critical details.\n\n" + conversation_text
                ),
            }],
            max_tokens=2000,
        )
        summary = response.content[0].text
        logger.info("auto_compact done  | session={} summary_len={}", session.id[:8], len(summary))

        if lib is not None:
            return lib.build_compact_messages(summary, transcript_ref)

        # lib 为 None 时保持原有行为（向后兼容）
        return [
            {"role": "user", "content": f"[Compressed. Transcript: {transcript_ref}]\n\n{summary}"},
            {"role": "assistant", "content": "Understood. Continuing."},
        ]
