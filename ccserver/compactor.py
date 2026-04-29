"""
compactor — conversation history compression.

Three-level strategy:
  1. micro()         — truncate old tool_result payloads in-place (free, no LLM)
  2. needs_compact() — cheap token estimate to decide when to act
  3. compact()       — LLM-based summarisation; archives full transcript to disk

扩展：CompactionProvider Protocol
  通过 Compactor.set_provider(provider) 可替换压缩算法。
  自定义 provider 实现 summarize() 方法即可，无需修改 Compactor 核心逻辑。

  示例：
    class MyProvider:
        id = "my-compactor"
        label = "My Custom Compactor"
        async def summarize(self, messages, compression_ratio=0.5, signal=None, previous_summary="") -> str:
            return "summary text"

    Compactor.set_provider(MyProvider())
"""

import json
from typing import Protocol, runtime_checkable

from loguru import logger

from .config import MODEL, THRESHOLD, KEEP_RECENT
from .utils import estimate_tokens, get_block_attr
from .model import ModelAdapter


# ─── CompactionProvider Protocol ─────────────────────────────────────────────


@runtime_checkable
class CompactionProvider(Protocol):
    """
    对话压缩算法 Protocol。

    实现此协议可替换 Compactor 默认的 Anthropic LLM 压缩逻辑。
    典型用途：本地 LLM 压缩、向量摘要、规则摘要等。

    通过 Compactor.set_provider(provider) 注册，全局生效。
    通过 Compactor.reset_provider() 恢复默认 Anthropic 压缩。

    属性：
        id:    唯一标识（snake_case）
        label: 人类可读名称

    方法：
        summarize(messages, compression_ratio, signal, previous_summary) -> str
            将消息列表压缩为摘要字符串。

    Args（summarize）：
        messages:          待压缩的消息列表（Anthropic 格式）
        compression_ratio: 目标压缩比（0.0-1.0），0.5 表示压缩至 50%，供参考
        signal:            取消信号（asyncio.Event 或 None）
        previous_summary:  上次压缩的摘要（增量压缩时使用），空字符串表示首次压缩

    Returns:
        摘要字符串，将作为 user 消息的正文注入压缩后的对话中。
    """

    id: str
    label: str

    async def summarize(
        self,
        messages: list,
        compression_ratio: float = 0.5,
        signal=None,
        previous_summary: str = "",
    ) -> str:
        ...


# 全局 CompactionProvider（None = 使用内置 Anthropic 压缩）
_custom_provider: CompactionProvider | None = None


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

    # ── Provider 注册 ─────────────────────────────────────────────────────────

    @staticmethod
    def set_provider(provider: CompactionProvider) -> None:
        """
        注册自定义压缩 provider，全局生效（替换默认 Anthropic 实现）。

        Args:
            provider: 实现 CompactionProvider Protocol 的对象。
        """
        global _custom_provider
        assert isinstance(provider, CompactionProvider), (
            f"provider 必须实现 CompactionProvider Protocol，got {type(provider)}"
        )
        _custom_provider = provider
        logger.info("CompactionProvider registered | id={} label={}", provider.id, provider.label)

    @staticmethod
    def reset_provider() -> None:
        """恢复默认 Anthropic LLM 压缩，清除自定义 provider。"""
        global _custom_provider
        _custom_provider = None
        logger.info("CompactionProvider reset to default (Anthropic LLM)")

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
            content = result.get("content")
            tid = result.get("tool_use_id", "")
            tool_name = tool_name_map.get(tid, "unknown")

            # 普通字符串结果：超过 100 字符才截断
            if isinstance(content, str) and len(content) > 100:
                result["content"] = f"[Previous: used {tool_name}]"
                truncated += 1

            # 多模态结果（list，含图像 base64）：只保留 text block，丢弃图像
            # 图像 base64 通常几十万字符，不截断会导致每次截图后立即触发 compact
            elif isinstance(content, list) and any(b.get("type") == "image" for b in content):
                text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
                summary = " | ".join(p for p in text_parts if p) or tool_name
                result["content"] = f"[Previous: used {tool_name} — {summary}]"
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

        # ── 自定义 provider 优先 ──────────────────────────────────────────────
        if _custom_provider is not None:
            try:
                summary = await _custom_provider.summarize(
                    messages=msgs,
                    compression_ratio=0.5,
                    signal=None,
                    previous_summary="",
                )
                assert isinstance(summary, str) and summary, (
                    f"CompactionProvider.summarize() returned empty or non-string: {summary!r}"
                )
                logger.info(
                    "auto_compact done (custom provider) | id={} session={} summary_len={}",
                    _custom_provider.id, session.id[:8], len(summary),
                )
            except Exception as e:
                logger.error(
                    "CompactionProvider.summarize() failed | id={} error={}, falling back to Anthropic LLM",
                    _custom_provider.id, e,
                )
                summary = await self._summarize_with_llm(msgs)
        else:
            summary = await self._summarize_with_llm(msgs)

        logger.info("auto_compact done  | session={} summary_len={}", session.id[:8], len(summary))

        if lib is not None:
            return lib.build_compact_messages(summary, transcript_ref)

        # lib 为 None 时保持原有行为（向后兼容）
        return [
            {"role": "user", "content": f"[Compressed. Transcript: {transcript_ref}]\n\n{summary}"},
            {"role": "assistant", "content": "Understood. Continuing."},
        ]

    async def _summarize_with_llm(self, msgs: list) -> str:
        """
        使用 Anthropic LLM 对消息列表进行摘要压缩。

        Args:
            msgs: 待压缩的消息列表（Anthropic 格式）

        Returns:
            摘要字符串
        """
        # 将消息序列化为文本，截断至 80000 字符避免超出 token 限制
        conversation_text = json.dumps(msgs, default=str)[:80000]

        # 部分模型（如 claude-3-5-sonnet）不支持 thinking 参数，显式关闭
        create_kwargs: dict = {"thinking": {"type": "disabled"}}

        response = await self.adapter.create(
            model=self.model,
            messages=[{"role": "user", "content": (
                "Summarize this conversation for continuity. Include: "
                "1) What was accomplished, 2) Current state, 3) Key decisions. "
                "Be concise but preserve critical details.\n\n" + conversation_text
            )}],
            max_tokens=2000,
            **create_kwargs,
        )

        assert response.content, (
            f"LLM returned empty content in Compactor._summarize_with_llm for model={self.model}"
        )

        # 优先取 TextBlock
        text_block = next(
            (b for b in response.content if getattr(b, "type", None) == "text"),
            None,
        )
        if text_block is not None:
            return text_block.text

        # 部分模型返回 ThinkingBlock（如开启了 extended thinking 的模型）
        thinking_block = next(
            (b for b in response.content if getattr(b, "type", None) == "thinking"),
            None,
        )
        assert thinking_block is not None, (
            f"Compactor._summarize_with_llm: no TextBlock or ThinkingBlock in response "
            f"content={response.content!r}"
        )
        logger.warning("auto_compact: no TextBlock found, falling back to ThinkingBlock for summary")
        return getattr(thinking_block, "thinking", "") or getattr(thinking_block, "text", "")
