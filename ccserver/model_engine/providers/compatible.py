"""ccserver/model_engine/providers/compatible.py

兼容 Provider 集合 — 用于接入任意 OpenAI/Anthropic/Responses API 兼容服务。

提供三个 Provider 类：
  1. CompatibleOpenAIProvider   — 任意 OpenAI Chat Completions 兼容服务
  2. CompatibleAnthropicProvider — 任意 Anthropic Messages API 兼容服务
  3. CompatibleResponsesAPIProvider — OpenAI Responses API 兼容服务（骨架）

使用场景：
  - 私有部署的 OpenAI 兼容 API（LMStudio、OneAPI、LocalAI 等）
  - 第三方 Anthropic 兼容代理
  - OpenRouter 等多模型聚合服务

使用方式：
    # OpenAI 兼容
    provider = CompatibleOpenAIProvider.from_config(
        base_url="http://localhost:1234/v1",
        api_key="lm-studio",
    )

    # Anthropic 兼容
    provider = CompatibleAnthropicProvider.from_config(
        base_url="https://my-anthropic-proxy.com",
        api_key="sk-...",
    )

Compatible Provider collection — for connecting to arbitrary OpenAI/Anthropic-compatible services.
"""

from __future__ import annotations

from ccserver.model_engine.adapters.chat_completions import ChatCompletionsAdapter
from ccserver.model_engine.adapters.anthropic_sdk import AnthropicSDKAdapter
from ccserver.model_engine.codecs.chat_completions import ChatCompletionsCodec
from ccserver.model_engine.codecs.anthropic import AnthropicCodec
from .base import BaseLLMProvider


class CompatibleOpenAIProvider(BaseLLMProvider):
    """
    任意 OpenAI Chat Completions 兼容服务 Provider。

    使用 ChatCompletionsAdapter + ChatCompletionsCodec。
    适用于：LMStudio、OneAPI、LocalAI、OpenRouter 等所有 OpenAI 兼容服务。

    Provider for any OpenAI Chat Completions-compatible service.
    Uses ChatCompletionsAdapter + ChatCompletionsCodec.
    """

    @classmethod
    def from_config(
        cls,
        base_url: "str | None" = None,
        api_key: "str | None" = None,
    ) -> "CompatibleOpenAIProvider":
        """
        根据 base_url 和 api_key 创建实例。

        Args:
            base_url: 服务端点 URL。None 时使用 OpenAI 官方端点。
            api_key:  API 密钥。None 时使用空字符串（适用于本地无鉴权服务）。

        Returns:
            CompatibleOpenAIProvider — 已初始化的 provider 实例
        """
        adapter = ChatCompletionsAdapter.from_config(
            base_url=base_url,
            api_key=api_key,
        )
        codec = ChatCompletionsCodec()
        return cls(adapter=adapter, codec=codec)


class CompatibleAnthropicProvider(BaseLLMProvider):
    """
    任意 Anthropic Messages API 兼容服务 Provider。

    使用 AnthropicSDKAdapter + AnthropicCodec。
    适用于：Anthropic API 代理、DeepSeek Anthropic 兼容接口等。

    Provider for any Anthropic Messages API-compatible service.
    Uses AnthropicSDKAdapter + AnthropicCodec.
    """

    @classmethod
    def from_config(
        cls,
        base_url: "str | None" = None,
        api_key: "str | None" = None,
    ) -> "CompatibleAnthropicProvider":
        """
        根据 base_url 和 api_key 创建实例。

        Args:
            base_url: 服务端点 URL。None 时使用 Anthropic 官方端点。
            api_key:  API 密钥。None 时 SDK 从环境变量 ANTHROPIC_API_KEY 读取。

        Returns:
            CompatibleAnthropicProvider — 已初始化的 provider 实例
        """
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(
            base_url=base_url,
            api_key=api_key or "",
        )
        adapter = AnthropicSDKAdapter(client=client)
        codec = AnthropicCodec()
        return cls(adapter=adapter, codec=codec)

    @classmethod
    def from_client(
        cls,
        client,
    ) -> "CompatibleAnthropicProvider":
        """
        根据已构造的 AsyncAnthropic client 创建实例（测试友好，便于注入 mock）。

        Args:
            client: 已初始化的 AsyncAnthropic 实例（或 mock）。

        Returns:
            CompatibleAnthropicProvider — 已初始化的 provider 实例

        Create instance from an existing AsyncAnthropic client (test-friendly).
        """
        assert client is not None, "CompatibleAnthropicProvider.from_client: client 不能为 None"
        adapter = AnthropicSDKAdapter(client=client)
        codec = AnthropicCodec()
        return cls(adapter=adapter, codec=codec)


class CompatibleResponsesAPIProvider(BaseLLMProvider):
    """
    OpenAI Responses API 兼容服务 Provider（骨架）。

    使用 ResponsesAPIAdapter + ResponsesCodec（均为骨架，待后续实现）。
    当前 from_config 抛 NotImplementedError。

    Provider for OpenAI Responses API-compatible service (skeleton).
    Uses ResponsesAPIAdapter + ResponsesCodec (both skeletons, to be implemented later).
    """

    @classmethod
    def from_config(
        cls,
        base_url: "str | None" = None,
        api_key: "str | None" = None,
    ) -> "CompatibleResponsesAPIProvider":
        """
        根据 base_url 和 api_key 创建实例（骨架，待实现）。

        Args:
            base_url: 服务端点 URL。
            api_key:  API 密钥。

        Raises:
            NotImplementedError: ResponsesAPIAdapter + ResponsesCodec 尚未完整实现。
        """
        raise NotImplementedError(
            "CompatibleResponsesAPIProvider 待实现：需要 ResponsesAPIAdapter + ResponsesCodec"
        )
