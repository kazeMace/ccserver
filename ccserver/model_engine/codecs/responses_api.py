"""ccserver/model_engine/codecs/responses_api.py

ResponsesCodec — OpenAI Responses API 编解码器（骨架）。

OpenAI Responses API 是新一代 API（区别于 Chat Completions），
使用不同的消息格式和响应结构。

此 Codec 直接继承 ProtocolCodec，需实现所有 4 个抽象方法。
当前为骨架实现，全部抛 NotImplementedError，待后续补全。

ResponsesCodec — OpenAI Responses API codec (skeleton).
Inherits ProtocolCodec directly (not ChatCompletionsCodec, as the format differs).
All abstract methods raise NotImplementedError until implemented.
"""

from __future__ import annotations

from ccserver.messages import UnifiedResponse, UnifiedStreamDelta, StreamState
from .base import ProtocolCodec


class ResponsesCodec(ProtocolCodec):
    """
    OpenAI Responses API 编解码器（骨架，待后续实现）。

    直接继承 ProtocolCodec（不继承 ChatCompletionsCodec），
    因为 Responses API 使用与 Chat Completions 不同的消息格式。

    OpenAI Responses API codec (skeleton, to be implemented later).
    Inherits ProtocolCodec directly; Responses API format differs from Chat Completions.
    """

    def encode_messages(self, messages: list, system: "str | None" = None) -> dict:
        """
        unified messages → Responses API 消息格式（骨架，待实现）。

        Args:
            messages: list[UnifiedMessage] — 统一消息列表
            system: str | None — 系统提示

        Raises:
            NotImplementedError: 当前版本未实现。
        """
        raise NotImplementedError("ResponsesCodec.encode_messages 待实现")

    def encode_tools(self, tools: "list | None") -> dict:
        """
        unified tool 定义 → Responses API tools 格式（骨架，待实现）。

        Args:
            tools: list | None — 工具定义列表

        Raises:
            NotImplementedError: 当前版本未实现。
        """
        raise NotImplementedError("ResponsesCodec.encode_tools 待实现")

    def decode_response(self, native_response) -> UnifiedResponse:
        """
        Responses API 完整响应 → UnifiedResponse（骨架，待实现）。

        Args:
            native_response: Responses API 响应对象

        Raises:
            NotImplementedError: 当前版本未实现。
        """
        raise NotImplementedError("ResponsesCodec.decode_response 待实现")

    def decode_stream_chunk(
        self, chunk, stream_state: StreamState
    ) -> "UnifiedStreamDelta | None":
        """
        Responses API 流式 chunk → UnifiedStreamDelta（骨架，待实现）。

        Args:
            chunk: Responses API 流式 chunk 对象
            stream_state: 可变累积状态

        Raises:
            NotImplementedError: 当前版本未实现。
        """
        raise NotImplementedError("ResponsesCodec.decode_stream_chunk 待实现")
