"""
ccserver/model_engine/codecs/base.py

ProtocolCodec ABC — 双向编解码器，unified ↔ native，无 client，纯函数风格。

设计说明：
  - encode 方向（unified → native）：把 UnifiedMessage 列表和工具定义转换为
    各 provider 原生格式，返回 dict（merge 进 native_params）。
  - decode 方向（native → unified）：把 provider 原生响应/流式 chunk 解码为
    UnifiedResponse / UnifiedStreamDelta。
  - Codec 本身不持有 SDK client，只做纯数据转换（无 IO、无副作用）。
  - 子类须实现所有 @abstractmethod，可选 override hook 方法。

ProtocolCodec ABC — bidirectional codec, unified ↔ native, no client, pure-function style.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ccserver.messages import (
    StreamState,
    UnifiedStreamDelta,
    UnifiedResponse,
    UnifiedUsage,
)


class ProtocolCodec(ABC):
    """
    双向编解码器抽象基类：unified ↔ provider native 格式互转。
    Bidirectional codec ABC: converts between unified and provider native formats.

    encode 方向（unified → native）：
        encode_messages / encode_tools / encode_thinking
        pre_encode_hook（消息预处理）/ post_encode_hook（params 后处理）

    decode 方向（native → unified）：
        decode_response / decode_stream_chunk
        finish_reason_hook / thinking_decode_hook / text_decode_hook /
        tool_decode_hook / _build_usage
    """

    # ── Encode 方向（unified → native）──────────────────────────────────────

    @abstractmethod
    def encode_messages(self, messages: list, system: "str | None" = None) -> dict:
        """
        unified messages → native 消息格式 dict（merge 进 native_params）。

        参数：
            messages: list[UnifiedMessage] — 统一消息列表
            system: str | None — 系统提示（各 provider 处理方式不同）

        返回：
            dict — 可直接 merge 进 native_params 的字典
                   通常含 "messages" 键，部分 provider 还含 "system" 键
        """

    @abstractmethod
    def encode_tools(self, tools: "list | None") -> dict:
        """
        unified tool 定义 → native tools 格式 dict（空列表/None → {}）。

        参数：
            tools: list | None — 工具定义列表（unified 格式）

        返回：
            dict — 含 "tools" 键的 dict，或空 dict（无工具时）
        """

    def encode_thinking(self, config) -> dict:
        """
        ThinkingConfig → native 推理参数 dict（默认返回 {}）。

        参数：
            config: ThinkingConfig — 推理配置

        返回：
            dict — provider 原生推理参数，或空 dict（不支持时）
        """
        # 默认不处理 thinking 配置，子类可 override
        return {}

    def pre_encode_hook(self, messages: list) -> list:
        """
        消息编码前预处理（sanitize、过滤内部 block 等），默认原样返回。
        Pre-encode hook for message sanitization or filtering internal blocks.

        参数：
            messages: list[UnifiedMessage] — 待处理消息列表

        返回：
            list[UnifiedMessage] — 处理后的消息列表
        """
        return messages

    def post_encode_hook(self, params: dict) -> dict:
        """
        encode 后追加 provider 特有 kwargs，默认原样返回。
        Post-encode hook to append provider-specific kwargs.

        参数：
            params: dict — 已编码的 native params

        返回：
            dict — 追加 provider 特有字段后的 params
        """
        return params

    # ── Decode 方向（native → unified）──────────────────────────────────────

    @abstractmethod
    def decode_response(self, native_response) -> UnifiedResponse:
        """
        native 完整响应 → UnifiedResponse。
        Decode provider native response into a UnifiedResponse.

        参数：
            native_response: provider SDK 返回的完整响应对象

        返回：
            UnifiedResponse — 统一响应对象
        """

    @abstractmethod
    def decode_stream_chunk(self, chunk, stream_state: StreamState) -> "UnifiedStreamDelta | None":
        """
        native 流式 chunk → UnifiedStreamDelta。
        返回 None 表示该 chunk 不产出用户可见 delta（如 tool_calls 累积 chunk）。

        Decode a native streaming chunk into a UnifiedStreamDelta, or None if the chunk
        does not produce a user-visible delta (e.g. tool_calls accumulation chunk).

        stream_state：ProviderStream 传入的可变状态，Codec 负责累积：
          - text_chunks.append(text)         — 正文文本片段
          - thinking_chunks.append(thinking)  — 思考链片段
          - tool_calls_raw[idx] = {...}        — 工具调用片段（按 index 累积）
          - stop_reason_raw = raw_reason       — 停止原因
          - usage_raw = raw_usage              — 用量数据

        参数：
            chunk: provider SDK 返回的流式 chunk 对象
            stream_state: 可变累积状态（Codec 负责向其写入）

        返回：
            UnifiedStreamDelta | None
        """

    def finish_reason_hook(self, raw_reason: "str | None") -> str:
        """
        native stop reason → 统一 stop reason。
        Map provider native finish/stop reason to unified stop reason.

        内置映射：stop → end_turn，tool_calls → tool_use，length → max_tokens。
        子类可 override 添加 provider 特有映射。

        参数：
            raw_reason: provider 原生停止原因字符串（或 None）

        返回：
            str — 统一 stop reason，未匹配时返回 "end_turn"
        """
        mapping = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }
        return mapping.get(raw_reason or "", "end_turn")

    def thinking_decode_hook(self, native_response) -> tuple:
        """
        从 native 响应提取 (thinking_text, signature)，默认 ("", None)。
        Extract (thinking_text, signature) from native response, default ("", None).

        参数：
            native_response: provider SDK 返回的响应对象

        返回：
            tuple[str, str | None] — (thinking_text, signature)
        """
        return ("", None)

    def text_decode_hook(self, raw_text: str) -> str:
        """
        正文文本后处理（剥离 <think> 等），默认原样返回。
        Post-process body text (e.g. strip <think> tags); default is identity.

        参数：
            raw_text: 原始正文文本

        返回：
            str — 处理后的文本
        """
        return raw_text

    def tool_decode_hook(self, raw_tool_calls) -> list:
        """
        raw tool_calls → list[UnifiedToolCall]，默认返回 []。
        Decode raw tool calls into list[UnifiedToolCall]; default returns [].

        参数：
            raw_tool_calls: provider 原生工具调用对象列表

        返回：
            list[UnifiedToolCall] — 统一工具调用列表
        """
        return []

    def _build_usage(self, usage_raw) -> "UnifiedUsage | None":
        """
        usage_raw → UnifiedUsage，None → None。
        子类 override 处理各 provider 格式。
        Build UnifiedUsage from raw usage object; returns None if usage_raw is None.
        Subclasses should override to handle provider-specific formats.

        参数：
            usage_raw: provider 原生用量对象（或 None）

        返回：
            UnifiedUsage | None
        """
        return None
