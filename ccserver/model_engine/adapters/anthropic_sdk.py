"""AnthropicSDKAdapter — 基于 Anthropic SDK 的 ProtocolAdapter 实现。

持有 AsyncAnthropic client，call/stream 直接透传参数给 SDK。
不做任何格式转换，由上层 Codec 负责。
keepalive_expiry=5：防止长耗时操作后连接被服务端关闭。

设计说明：
- 此类只做"发请求"一件事（SRP）
- 参数透传：native_params 由上层 Codec 产出，直接 **解包 传入 SDK，不做修改
- 返回 raw SDK 对象，不做格式转换
"""

from __future__ import annotations
from typing import Any

from anthropic import AsyncAnthropic
from loguru import logger

from .base import ProtocolAdapter


class AnthropicSDKAdapter(ProtocolAdapter):
    """封装 AsyncAnthropic 客户端，实现 ProtocolAdapter 接口。

    只负责将参数透传给 Anthropic SDK，不做任何格式转换。
    格式转换（unified ↔ native）由上层 Codec 负责。
    """

    def __init__(self, client: AsyncAnthropic):
        """
        初始化 AnthropicSDKAdapter。

        Args:
            client: AsyncAnthropic 客户端实例，不能为 None。
        """
        assert client is not None, "AnthropicSDKAdapter: client 不能为 None"
        self._client = client

    async def call(self, **native_params: Any) -> Any:
        """
        非流式调用，返回 SDK 原始 Message 对象（不转换）。

        Args:
            **native_params: Anthropic SDK messages.create 接受的参数，
                             例如 model、messages、max_tokens、system 等。

        Returns:
            anthropic.types.Message — SDK 原始响应，不做任何格式转换。
        """
        logger.debug("AnthropicSDKAdapter.call | model={}", native_params.get("model"))
        return await self._client.messages.create(**native_params)

    def stream(self, **native_params: Any) -> Any:
        """
        流式调用，返回 SDK 原生 stream context manager。

        Args:
            **native_params: Anthropic SDK messages.stream 接受的参数，
                             例如 model、messages、max_tokens、system 等。

        Returns:
            Anthropic SDK 原生 stream context manager（MessageStream），
            由上层 ProviderStream 包装后迭代使用。
        """
        logger.debug("AnthropicSDKAdapter.stream | model={}", native_params.get("model"))
        return self._client.messages.stream(**native_params)
