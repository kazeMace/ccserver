"""
ccserver/model_engine/codecs/anthropic.py

AnthropicCodec — unified ↔ Anthropic Messages API 双向编解码器。

encode 方向：
  - encode_messages: UnifiedMessage 列表 → Anthropic native 消息列表
    system 独立作为 "system" 键返回（{"system": ..., "messages": [...]}）
  - encode_tools: 透传（Anthropic native = 通用 {name, description, input_schema} 格式）
  - encode_thinking: ThinkingConfig → {"thinking": {"type": "adaptive"/"disabled", ...}}

decode 方向：
  - decode_response: SDK Message → UnifiedResponse
  - decode_stream_chunk: SDK content_block_delta → UnifiedStreamDelta | None
  - _build_usage: SDK usage → UnifiedUsage（含 cache 字段）

设计约束：
  - 不 import Anthropic SDK，只通过 getattr 读取 SDK 对象属性
  - 测试用 MagicMock 即可覆盖，无需真实 SDK

AnthropicCodec — bidirectional codec for Anthropic Messages API.
"""

from __future__ import annotations

from loguru import logger

from ccserver.messages import (
    UnifiedTextBlock,
    UnifiedThinkingBlock,
    UnifiedToolUseBlock,
    UnifiedToolResultBlock,
    UnifiedImageBlock,
    UnifiedImageThumbnailBlock,
    UnifiedCommandBlock,
    UnifiedResponse,
    UnifiedStreamDelta,
    UnifiedToolCall,
    UnifiedUsage,
    StreamState,
)
from .base import ProtocolCodec


class AnthropicCodec(ProtocolCodec):
    """
    Anthropic Messages API 双向编解码器。
    Bidirectional codec for the Anthropic Messages API.
    """

    # ── Encode 方向（unified → native）──────────────────────────────────────

    def encode_messages(self, messages: list, system: "str | None" = None) -> dict:
        """
        unified messages → Anthropic native 消息格式 dict。

        Anthropic API 的特殊点：
          - system 作为顶层独立字段（不塞进 messages 列表）
          - content 始终是 list（不简化为 str）

        参数：
            messages: list[UnifiedMessage] — 统一消息列表
            system: str | None — 系统提示（单独作为 "system" 键）

        返回：
            dict — {"messages": [...]} 或 {"system": ..., "messages": [...]}
        """
        native_messages = []

        for msg in messages:
            # 兼容 UnifiedMessage 和裸 dict 两种形态
            if isinstance(msg, dict):
                native_messages.append(msg)
                continue

            role = msg.role
            # 把 UnifiedBlock 列表编码为 Anthropic native content list
            native_content = self._encode_content_blocks(msg.content)
            native_messages.append({"role": role, "content": native_content})

        result: dict = {"messages": native_messages}
        # system 单独作为顶层字段（Anthropic API 规范）
        if system is not None:
            result["system"] = system
        return result

    def _encode_content_blocks(self, blocks: list) -> list:
        """
        list[UnifiedBlock] → Anthropic native content list。
        过滤内部 block（UnifiedImageThumbnailBlock、UnifiedCommandBlock）。

        参数：
            blocks: list[UnifiedBlock]

        返回：
            list[dict] — Anthropic native content list
        """
        native_content = []
        for block in blocks:
            # 内部 block：不发给 Anthropic API，直接跳过
            if isinstance(block, UnifiedImageThumbnailBlock):
                logger.debug("AnthropicCodec: 过滤 ImageThumbnailBlock（不发给 API）")
                continue
            if isinstance(block, UnifiedCommandBlock):
                logger.debug("AnthropicCodec: 过滤 CommandBlock（不发给 API）")
                continue

            # TextBlock → {"type": "text", "text": ...}
            if isinstance(block, UnifiedTextBlock):
                native_content.append({"type": "text", "text": block.text})

            # ThinkingBlock → {"type": "thinking", "thinking": ..., "signature": ...}（有签名才加）
            elif isinstance(block, UnifiedThinkingBlock):
                native_block: dict = {"type": "thinking", "thinking": block.thinking}
                if block.signature is not None:
                    native_block["signature"] = block.signature
                native_content.append(native_block)

            # ToolUseBlock → {"type": "tool_use", "id": ..., "name": ..., "input": ...}
            elif isinstance(block, UnifiedToolUseBlock):
                native_content.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

            # ToolResultBlock → {"type": "tool_result", "tool_use_id": ..., "content": ..., "is_error": ...}
            elif isinstance(block, UnifiedToolResultBlock):
                native_content.append({
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": block.content,
                    "is_error": block.is_error,
                })

            # ImageBlock → {"type": "image", "source": ...}
            elif isinstance(block, UnifiedImageBlock):
                native_content.append({"type": "image", "source": block.source})

            # 其他 block：调用 to_dict()（透传）
            else:
                native_content.append(block.to_dict())

        return native_content

    def encode_tools(self, tools: "list | None") -> dict:
        """
        Anthropic native tools 格式 = 通用格式（input_schema），透传。
        空列表/None → {}。

        参数：
            tools: list | None — 工具定义列表

        返回：
            dict — {"tools": tools} 或 {}
        """
        if not tools:
            return {}
        return {"tools": tools}

    def encode_thinking(self, config) -> dict:
        """
        ThinkingConfig → Anthropic native thinking 参数。

        ThinkingConfig.enabled=True  → {"thinking": {"type": "adaptive", ...}}
        ThinkingConfig.enabled=False → {"thinking": {"type": "disabled"}}

        参数：
            config: ThinkingConfig — 推理配置

        返回：
            dict — Anthropic native thinking 参数
        """
        assert config is not None, "encode_thinking: config 不能为 None"

        if not config.enabled:
            return {"thinking": {"type": "disabled"}}

        # enabled=True：Anthropic API 使用 "adaptive" type
        thinking_params: dict = {"type": "adaptive"}

        # effort 映射为 budget_tokens（如有需要可扩展）
        # 目前 Anthropic API 支持 budget_tokens 参数
        # effort → budget_tokens 映射（近似）
        effort_to_budget = {
            "low": 1024,
            "medium": 4096,
            "high": 10000,
            "xhigh": 20000,
            "max": 32000,
        }
        budget = effort_to_budget.get(getattr(config, "effort", "high"), 10000)
        thinking_params["budget_tokens"] = budget

        return {"thinking": thinking_params}

    # ── Decode 方向（native → unified）──────────────────────────────────────

    def decode_response(self, native_response) -> UnifiedResponse:
        """
        Anthropic SDK Message → UnifiedResponse。

        block 类型处理：
          - text      → content（str，多块拼接）
          - thinking  → thinking（str）
          - tool_use  → tool_calls（list[UnifiedToolCall]）
          - 其他      → 记 warning，跳过（不抛异常，避免回归）

        参数：
            native_response: Anthropic SDK Message 对象

        返回：
            UnifiedResponse — 统一响应
        """
        assert native_response is not None, "decode_response: native_response 不能为 None"

        content_parts: list[str] = []      # 正文文本片段（多块时拼接）
        thinking_parts: list[str] = []     # 思考链文本片段
        tool_calls: list[UnifiedToolCall] = []

        for block in getattr(native_response, "content", []) or []:
            block_type = getattr(block, "type", None)

            if block_type == "text":
                text = getattr(block, "text", "")
                content_parts.append(self.text_decode_hook(text))

            elif block_type == "thinking":
                thinking = getattr(block, "thinking", "")
                thinking_parts.append(thinking)

            elif block_type == "tool_use":
                tool_calls.append(UnifiedToolCall(
                    id=getattr(block, "id", ""),
                    name=getattr(block, "name", ""),
                    input=getattr(block, "input", {}) or {},
                ))

            else:
                # 未知 block type：记 warning，不抛异常（向前兼容）
                logger.warning(
                    "AnthropicCodec.decode_response: 跳过未知 block type={}",
                    block_type,
                )

        stop_reason = getattr(native_response, "stop_reason", None) or "end_turn"
        usage = self._build_usage(getattr(native_response, "usage", None))

        return UnifiedResponse(
            content="".join(content_parts),
            thinking="".join(thinking_parts),
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
        )

    def decode_stream_chunk(
        self, chunk, stream_state: StreamState
    ) -> "UnifiedStreamDelta | None":
        """
        Anthropic SDK 流式 chunk → UnifiedStreamDelta | None。

        只处理 content_block_delta 类型；其他 chunk 类型直接返回 None。

        delta 类型：
          - text_delta     → 累积 state.text_chunks + 返回 UnifiedStreamDelta("text", ...)
          - thinking_delta → 累积 state.thinking_chunks + 返回 UnifiedStreamDelta("thinking", ...)
          - 其他           → 返回 None

        参数：
            chunk: Anthropic SDK stream chunk 对象
            stream_state: 可变累积状态

        返回：
            UnifiedStreamDelta | None
        """
        # 只关心 content_block_delta 类型的 chunk
        if getattr(chunk, "type", None) != "content_block_delta":
            return None

        delta = getattr(chunk, "delta", None)
        if delta is None:
            return None

        delta_type = getattr(delta, "type", None)

        if delta_type == "text_delta":
            text = getattr(delta, "text", "")
            stream_state.text_chunks.append(text)
            return UnifiedStreamDelta(kind="text", text=text)

        elif delta_type == "thinking_delta":
            thinking = getattr(delta, "thinking", "")
            stream_state.thinking_chunks.append(thinking)
            return UnifiedStreamDelta(kind="thinking", text=thinking)

        # 其他 delta type（input_json_delta 等）不产出用户可见 delta
        return None

    def _build_usage(self, usage_raw) -> "UnifiedUsage | None":
        """
        Anthropic SDK usage → UnifiedUsage（含 prompt caching 字段）。

        参数：
            usage_raw: Anthropic SDK usage 对象（或 None）

        返回：
            UnifiedUsage | None
        """
        if usage_raw is None:
            return None

        input_toks = getattr(usage_raw, "input_tokens", 0) or 0
        output_toks = getattr(usage_raw, "output_tokens", 0) or 0
        cache_read = getattr(usage_raw, "cache_read_input_tokens", 0) or 0
        cache_create = getattr(usage_raw, "cache_creation_input_tokens", 0) or 0

        return UnifiedUsage(
            input_tokens=input_toks,
            output_tokens=output_toks,
            total_tokens=input_toks + output_toks,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_create,
        )
