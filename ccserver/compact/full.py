"""
compact/full.py — LLM 摘要压缩组件（FullCompactor）。

职责：将超长对话历史通过 LLM 生成摘要，压缩为 2 条消息。

可扩展性：
  FullCompactor Protocol 定义接口，DefaultFullCompactor 是默认实现。
  SummarizationProvider Protocol 定义可替换的摘要算法：
    - 实例级：compactor_instance.set_provider(p)，只影响当前实例
    - 全局级：DefaultFullCompactor.set_global_provider(p)，影响所有未设置实例 provider 的实例
    - 优先级：实例 > 全局 > 内置 LLM

向后兼容：
  原 CompactionProvider Protocol 与 SummarizationProvider 接口完全相同，
  旧代码注册的 provider 无需修改即可使用（Protocol 是鸭子类型）。
"""

import json
from typing import Protocol, runtime_checkable

from loguru import logger

from ..config import MODEL
from ..model import ModelAdapter
from .strip import strip_images_from_messages


# ─── SummarizationProvider Protocol ──────────────────────────────────────────


@runtime_checkable
class SummarizationProvider(Protocol):
    """
    摘要算法协议（原 CompactionProvider，接口完全兼容）。

    实现此协议可替换默认的 Anthropic LLM 摘要逻辑。
    典型用途：本地 LLM、向量摘要、规则摘要等。

    属性：
        id:    唯一标识（snake_case）
        label: 人类可读名称

    方法：
        summarize(messages, compression_ratio, signal, previous_summary) -> str
            将消息列表压缩为摘要字符串。

    Args（summarize）：
        messages:          待压缩的消息列表（Anthropic 格式，已剥离图片）
        compression_ratio: 目标压缩比（0.0-1.0），供参考，0.5 = 压缩至 50%
        signal:            取消信号（asyncio.Event 或 None）
        previous_summary:  上次压缩的摘要（增量压缩时使用），空字符串表示首次

    Returns:
        摘要字符串，非空。
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


# ─── FullCompactor Protocol ───────────────────────────────────────────────────


@runtime_checkable
class FullCompactor(Protocol):
    """
    LLM 摘要压缩协议。

    实现此协议可完全替换默认的 full compact 逻辑（包括归档、摘要、消息构建）。

    方法：
        compact(messages, session, emitter, lib) -> list
            将消息列表压缩，返回压缩后的消息列表（通常 2 条）。
    """

    async def compact(
        self,
        messages: list,
        session,
        emitter,
        lib=None,
    ) -> list:
        ...


# ─── DefaultFullCompactor ─────────────────────────────────────────────────────


# 全局 provider（None = 使用内置 Anthropic LLM）
# 通过 DefaultFullCompactor.set_global_provider() 设置
_global_provider: SummarizationProvider | None = None


class DefaultFullCompactor:
    """
    默认 LLM 摘要压缩实现。

    执行步骤：
    1. session.save_transcript() — 归档全量历史到磁盘（不丢数据）
    2. strip_images_from_messages() — 剥离图片/文档（防止压缩请求本身 prompt-too-long）
    3. 调用 SummarizationProvider（或内置 LLM）生成摘要
    4. lib.build_compact_messages() 或默认 2 条消息格式写回

    Provider 优先级：实例级 > 全局级 > 内置 Anthropic LLM

    Args:
        adapter: ModelAdapter 实例，用于调用 LLM。
        model:   摘要使用的模型 ID，默认 config.MODEL。
    """

    def __init__(self, adapter: ModelAdapter, model: str = MODEL):
        self.adapter = adapter
        self.model = model
        # 实例级 provider（优先于全局）
        self._instance_provider: SummarizationProvider | None = None

    # ── 实例级 provider 管理 ──────────────────────────────────────────────────

    def set_provider(self, provider: SummarizationProvider) -> None:
        """
        注册实例级摘要 provider，只影响当前 compactor 实例。

        Args:
            provider: 实现 SummarizationProvider Protocol 的对象。
        """
        assert isinstance(provider, SummarizationProvider), (
            f"provider 必须实现 SummarizationProvider Protocol，got {type(provider)}"
        )
        self._instance_provider = provider
        logger.info(
            "FullCompactor instance provider set | id={} label={}",
            provider.id, provider.label,
        )

    def reset_provider(self) -> None:
        """清除实例级 provider，降级到全局或内置 LLM。"""
        self._instance_provider = None
        logger.info("FullCompactor instance provider reset")

    # ── 全局 provider 管理 ────────────────────────────────────────────────────

    @classmethod
    def set_global_provider(cls, provider: SummarizationProvider) -> None:
        """
        注册全局摘要 provider，影响所有未设置实例 provider 的 DefaultFullCompactor 实例。

        Args:
            provider: 实现 SummarizationProvider Protocol 的对象。
        """
        global _global_provider
        assert isinstance(provider, SummarizationProvider), (
            f"provider 必须实现 SummarizationProvider Protocol，got {type(provider)}"
        )
        _global_provider = provider
        logger.info(
            "FullCompactor global provider set | id={} label={}",
            provider.id, provider.label,
        )

    @classmethod
    def reset_global_provider(cls) -> None:
        """清除全局 provider，所有实例降级到内置 LLM。"""
        global _global_provider
        _global_provider = None
        logger.info("FullCompactor global provider reset to default (Anthropic LLM)")

    # ── compact 主流程 ────────────────────────────────────────────────────────

    async def compact(
        self,
        messages: list,
        session,
        emitter,
        lib=None,
    ) -> list:
        """
        LLM 摘要压缩：归档 → 剥离图片 → 摘要 → 构建结果消息。

        Args:
            messages: 待压缩的消息列表。
            session:  Session 实例（用于 save_transcript / rewrite_messages）。
            emitter:  BaseEmitter 实例（用于推送进度事件）。
            lib:      PromptEngine 实例，控制压缩后消息格式；为 None 时使用默认格式。

        Returns:
            压缩后的消息列表（通常 2 条：摘要 user + 确认 assistant）。
        """
        from ..utils.sdk import estimate_tokens as sdk_estimate  # 避免循环 import
        logger.info(
            "FullCompactor.compact start | session={} messages={} tokens~{}",
            session.id[:8], len(messages), sdk_estimate(messages),
        )

        # Step 1: 归档原始历史（不丢数据，即使后续 LLM 摘要失败也可恢复）
        transcript_ref = session.save_transcript(messages)
        logger.debug("Transcript archived | ref={}", transcript_ref)
        await emitter.emit_compact(f"saved transcript: {transcript_ref}")

        # Step 2: 剥离图片/文档（防止摘要请求本身 prompt-too-long）
        stripped_messages = strip_images_from_messages(messages)

        # Step 3: 选择摘要算法（实例 > 全局 > 内置 LLM）
        provider = self._instance_provider or _global_provider
        if provider is not None:
            summary = await self._summarize_with_provider(provider, stripped_messages)
        else:
            summary = await self._summarize_with_llm(stripped_messages)

        logger.info(
            "FullCompactor.compact done | session={} summary_len={}",
            session.id[:8], len(summary),
        )

        # Step 4: 构建压缩后消息（lib 控制格式）
        if lib is not None:
            return lib.build_compact_messages(summary, transcript_ref)

        # 无 lib 时的默认格式（向后兼容）
        return [
            {"role": "user", "content": f"[Compressed. Transcript: {transcript_ref}]\n\n{summary}"},
            {"role": "assistant", "content": "Understood. Continuing."},
        ]

    # ── 摘要实现 ─────────────────────────────────────────────────────────────

    async def _summarize_with_provider(
        self,
        provider: SummarizationProvider,
        messages: list,
    ) -> str:
        """
        使用自定义 provider 生成摘要，失败时 fallback 到内置 LLM。

        Args:
            provider: 摘要 provider 实例。
            messages: 已剥离图片的消息列表。

        Returns:
            摘要字符串。
        """
        try:
            summary = await provider.summarize(
                messages=messages,
                compression_ratio=0.5,
                signal=None,
                previous_summary="",
            )
            assert isinstance(summary, str) and summary, (
                f"SummarizationProvider.summarize() 返回空或非字符串: {summary!r}"
            )
            logger.info(
                "Summarization done (provider) | id={} summary_len={}",
                provider.id, len(summary),
            )
            return summary
        except Exception as e:
            logger.error(
                "SummarizationProvider.summarize() failed | id={} error={}, "
                "falling back to Anthropic LLM",
                provider.id, e,
            )
            return await self._summarize_with_llm(messages)

    async def _summarize_with_llm(self, messages: list) -> str:
        """
        使用 Anthropic LLM 对消息列表进行摘要压缩（内置默认实现）。

        Args:
            messages: 已剥离图片的消息列表。

        Returns:
            摘要字符串。
        """
        # 按消息边界截断，保证 JSON 完整性（避免截到 JSON 中间）
        conversation_text = _truncate_messages_to_chars(messages, max_chars=80000)

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
            f"LLM 返回空 content | model={self.model}"
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
            f"LLM response 中没有 TextBlock 或 ThinkingBlock | content={response.content!r}"
        )
        logger.warning("full_compact: no TextBlock, falling back to ThinkingBlock for summary")
        return getattr(thinking_block, "thinking", "") or getattr(thinking_block, "text", "")


# ─── 辅助函数 ──────────────────────────────────────────────────────────────────


def _truncate_messages_to_chars(messages: list, max_chars: int) -> str:
    """
    将消息列表序列化为 JSON 字符串，按消息边界截断至 max_chars 字符。

    不同于硬截 JSON 字符串（可能截到对象中间），
    此函数从尾部逐条删减消息，保证输出是合法 JSON。
    优先保留最新消息（从末尾开始保留）。

    Args:
        messages: 消息列表。
        max_chars: 目标最大字符数。

    Returns:
        序列化后的 JSON 字符串，长度 <= max_chars。
    """
    # 先尝试全量序列化
    full_text = json.dumps(messages, default=str)
    if len(full_text) <= max_chars:
        return full_text

    # 超出限制：从头部删减（保留最新的消息）
    kept = list(messages)
    while kept:
        text = json.dumps(kept, default=str)
        if len(text) <= max_chars:
            logger.debug(
                "_truncate_messages_to_chars: kept {}/{} messages ({} chars)",
                len(kept), len(messages), len(text),
            )
            return text
        # 删掉最旧的一条
        kept = kept[1:]

    # 极端情况：单条消息都超限，直接硬截
    logger.warning(
        "_truncate_messages_to_chars: single message exceeds limit, hard truncating"
    )
    return full_text[:max_chars]
