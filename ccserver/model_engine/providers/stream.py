"""ccserver/model_engine/providers/stream.py

ProviderStream — 流式响应包装器，async context manager + async iterator。

包装 adapter 返回的 raw stream，产出 UnifiedStreamDelta，最终组装 UnifiedResponse。

设计说明：
  - SRP：只负责"包装 raw stream + 驱动 Codec 解码 + 组装最终 response"
  - 不持有 SDK client，只持有 raw_stream 和 codec
  - async generator _deltas() 逐 chunk 调用 codec.decode_stream_chunk
  - get_final_response() 从 StreamState 组装 UnifiedResponse

ProviderStream — streaming response wrapper (async context manager + async iterator).
Wraps the raw adapter stream, yields UnifiedStreamDelta, and assembles the final UnifiedResponse.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from ccserver.messages import UnifiedResponse, UnifiedStreamDelta, StreamState
from ccserver.model_engine.codecs.base import ProtocolCodec


class ProviderStream:
    """
    包装 adapter 返回的 raw stream，产出 UnifiedStreamDelta，最终组装 UnifiedResponse。

    使用方式：
        async with provider.stream(...) as ps:
            async for delta in ps:
                print(delta.text, end="", flush=True)
            response = await ps.get_final_response()

    注意事项：
      - 必须先用 async with 进入（__aenter__），再用 async for 迭代
      - get_final_response() 可在迭代完成后调用，也可在未迭代完时调用（会自动 drain）

    Wraps the raw adapter stream, yields UnifiedStreamDelta, assembles final UnifiedResponse.
    Must be used as: async with provider.stream(...) as ps: async for delta in ps: ...
    """

    def __init__(self, raw_stream: Any, codec: ProtocolCodec):
        """
        初始化 ProviderStream。

        Args:
            raw_stream: adapter.stream() 返回的 SDK 原生 stream（async context manager）
            codec:      ProtocolCodec 实例，负责 decode_stream_chunk
        """
        assert raw_stream is not None, "ProviderStream: raw_stream 不能为 None"
        assert codec is not None, "ProviderStream: codec 不能为 None"

        self._raw_stream = raw_stream     # SDK 原生 stream（async context manager）
        self._codec = codec               # 解码器（decode_stream_chunk / 组装 response）
        self._state = StreamState()       # 可变累积状态（text_chunks / tool_calls 等）
        self._entered = None              # __aenter__ 后的底层迭代器对象

    async def __aenter__(self) -> "ProviderStream":
        """进入 async context：打开 SDK stream，返回 self。"""
        logger.debug("ProviderStream.__aenter__: 进入 raw stream")
        self._entered = await self._raw_stream.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """退出 async context：关闭 SDK stream。"""
        logger.debug("ProviderStream.__aexit__: 退出 raw stream")
        return await self._raw_stream.__aexit__(exc_type, exc_val, exc_tb)

    def __aiter__(self):
        """返回 async generator（_deltas）作为迭代器。"""
        return self._deltas()

    async def _deltas(self):
        """
        async generator：逐 chunk 调用 codec.decode_stream_chunk，产出 UnifiedStreamDelta。

        只产出 codec 返回非 None 的 delta（None 表示该 chunk 不产出用户可见内容）。

        Yields:
            UnifiedStreamDelta — 每个用户可见的增量事件
        """
        assert self._entered is not None, (
            "ProviderStream: 必须先用 'async with' 进入，再迭代。"
            " 请使用: async with provider.stream(...) as ps: async for delta in ps: ..."
        )

        async for chunk in self._entered:
            # codec 负责解码 chunk 并累积到 state，返回 delta 或 None
            # Codec decodes chunk, accumulates into state, returns delta or None
            delta = self._codec.decode_stream_chunk(chunk, self._state)
            if delta is not None:
                yield delta

    async def get_final_response(self) -> UnifiedResponse:
        """
        流结束后，从 StreamState 组装 UnifiedResponse。

        若流尚未耗尽，先自动 drain（消费完所有 chunk）。
        After stream ends, assemble UnifiedResponse from StreamState.
        If stream is not exhausted, drain it first.

        Returns:
            UnifiedResponse — 完整的统一响应对象

        Raises:
            AssertionError: 若未先进入 async context（__aenter__）
        """
        assert self._entered is not None, (
            "ProviderStream.get_final_response: 必须先用 'async with' 进入。"
        )

        # drain：消费所有剩余 chunk（已迭代完时，这里是 no-op）
        # Drain: consume all remaining chunks (no-op if already exhausted)
        async for _ in self:
            pass

        # 从累积的 StreamState 组装完整响应
        # Assemble final response from accumulated StreamState
        return UnifiedResponse(
            content="".join(self._state.text_chunks),
            thinking="".join(self._state.thinking_chunks),
            tool_calls=self._codec.tool_decode_hook(self._state.tool_calls_raw),
            stop_reason=self._codec.finish_reason_hook(self._state.stop_reason_raw),
            usage=self._codec._build_usage(self._state.usage_raw),
        )
