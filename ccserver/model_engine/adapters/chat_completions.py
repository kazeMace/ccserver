"""ChatCompletionsAdapter — 基于 OpenAI SDK 的 ProtocolAdapter 实现。

支持所有 OpenAI-compatible API：OpenAI、OpenRouter、Ollama(v1)、LMStudio、OneAPI 等。
只负责发请求返回 raw response，格式转换由 Codec 负责。

超时与连接复用：
  使用 make_async_http_client()，timeout=600s，keepalive_expiry=5，
  避免 MCP 长调用后连接被服务端关闭。
"""

from __future__ import annotations
from typing import Any

from loguru import logger

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None  # type: ignore

from .base import ProtocolAdapter
from ..wiring.http import make_async_http_client


class ChatCompletionsAdapter(ProtocolAdapter):
    """封装兼容 OpenAI Chat Completions 接口的异步客户端。

    职责：
    - 持有 AsyncOpenAI client（或兼容 SDK client）
    - call()：非流式调用，返回 raw SDK ChatCompletion 对象
    - stream()：流式调用，返回 SDK 原生 async stream（不包装）

    不做任何格式转换，格式转换由 Codec 负责（SRP 原则）。
    """

    def __init__(self, client: Any):
        """
        Args:
            client: AsyncOpenAI 实例（或兼容 OpenAI SDK 接口的 client）。
                    不能为 None。
        """
        assert client is not None, "ChatCompletionsAdapter: client 不能为 None"
        self._client = client

    @classmethod
    def from_config(
        cls,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> "ChatCompletionsAdapter":
        """根据 base_url 和 api_key 创建实例。

        适用于 OpenAI、OpenRouter、Ollama(v1)、LMStudio、OneAPI 等 OpenAI 兼容接口。

        Args:
            base_url: API 端点 URL。None 时使用 OpenAI 官方端点。
            api_key:  API 密钥。None 时使用空字符串（适用于本地无鉴权服务）。

        Returns:
            ChatCompletionsAdapter 实例。

        Raises:
            AssertionError: openai package 未安装时抛出。
        """
        assert AsyncOpenAI is not None, "openai package 未安装，请执行 pip install openai"
        client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "",
            http_client=make_async_http_client(),
        )
        return cls(client)

    async def call(self, **native_params: Any) -> Any:
        """非流式调用，返回 SDK 原始 ChatCompletion 对象（不转换）。

        会自动移除 stream 参数，确保以非流式模式调用。

        Args:
            **native_params: 由 Codec.encode 产出的参数字典，直接透传给 SDK。
                             例如：model, messages, max_tokens, tools, temperature 等。

        Returns:
            openai.types.chat.ChatCompletion：SDK 原始响应对象。
        """
        # 非流式调用确保不带 stream 参数（即使调用方误传也移除）
        native_params.pop("stream", None)
        logger.debug("ChatCompletionsAdapter.call | model={}", native_params.get("model"))
        return await self._client.chat.completions.create(**native_params)

    def stream(self, **native_params: Any) -> Any:
        """流式调用，返回 SDK 原生 async stream（不转换）。

        会自动设置 stream=True。

        Args:
            **native_params: 由 Codec.encode 产出的参数字典，直接透传给 SDK。

        Returns:
            SDK 原生 async stream，由上层 ProviderStream 迭代消费。
        """
        # 流式调用必须设置 stream=True
        native_params["stream"] = True
        logger.debug("ChatCompletionsAdapter.stream | model={}", native_params.get("model"))
        return self._client.chat.completions.create(**native_params)
