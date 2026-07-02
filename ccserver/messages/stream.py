"""
ccserver/messages/stream.py

流式解码相关数据类：UnifiedStreamDelta 和 StreamState。
零外部依赖（只用 dataclass 和标准库）。

Streaming decode data classes: UnifiedStreamDelta and StreamState.
Zero external dependencies (only dataclass and stdlib).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UnifiedStreamDelta:
    """
    流式增量事件，由 Codec.decode_stream_chunk 产出，ProviderStream.__aiter__ 向上抛出。
    Streaming delta event, produced by Codec.decode_stream_chunk and yielded by ProviderStream.__aiter__.

    Fields:
        kind: 增量类别，"text" 表示正文增量，"thinking" 表示思考链增量
              Delta category: "text" for body text, "thinking" for reasoning text
        text: 本次增量的文本内容
              The incremental text content for this delta

    使用方式 / Usage:
        async for delta in provider_stream:
            if delta.kind == "text":
                print(delta.text, end="", flush=True)
    """

    kind: str   # "text" | "thinking"
    text: str   # 本次增量文本 / Incremental text for this delta


@dataclass
class StreamState:
    """
    流式解码可变累积状态，由 Codec.decode_stream_chunk 写入，ProviderStream 读取。
    Mutable accumulation state for streaming decode, written by Codec.decode_stream_chunk
    and read by ProviderStream to assemble the final UnifiedResponse.

    Fields:
        text_chunks:      正文文本增量片段列表，最终用 "".join() 组装为完整 content
                          List of body text delta chunks; joined with "" to form final content
        thinking_chunks:  思考链文本增量片段列表，最终用 "".join() 组装为完整 thinking
                          List of reasoning text delta chunks; joined with "" to form final thinking
        tool_calls_raw:   工具调用原始数据，key 为调用索引（int），value 为 {id, name, arguments}
                          Raw tool call data; key is call index (int), value is {id, name, arguments}
        stop_reason_raw:  停止原因原始字符串，None 表示流尚未结束
                          Raw stop reason string, None means stream has not ended yet
        usage_raw:        provider 原生用量对象，None 表示尚未收到
                          Provider native usage object, None means not yet received

    使用方式 / Usage:
        state = StreamState()
        async for chunk in raw_stream:
            delta = codec.decode_stream_chunk(chunk, state)  # codec 写入 state
            if delta:
                yield delta
        # 流结束后，从 state 组装 UnifiedResponse
        # After stream ends, assemble UnifiedResponse from state
    """

    text_chunks: list = field(default_factory=list)         # list[str]：正文增量片段
    thinking_chunks: list = field(default_factory=list)     # list[str]：思考链增量片段
    tool_calls_raw: dict = field(default_factory=dict)      # index(int) → {id, name, arguments}
    stop_reason_raw: "str | None" = None                    # 停止原因原始值，None = 流未结束
    usage_raw: object = None                                # provider 原生用量对象
