"""ccserver/model_engine/providers/deepseek.py

DeepSeek Provider 集合 — 支持两种接入协议：
  1. DeepSeekChatProvider  — Chat Completions 协议（OpenAI 兼容）
  2. DeepSeekAnthropicProvider — Anthropic Messages API 兼容接口

DeepSeek 官方文档：https://api-docs.deepseek.com/

使用方式：
    # 方式一：Chat Completions 协议（推荐，大多数模型）
    provider = DeepSeekChatProvider.from_config(api_key="sk-...")

    # 方式二：Anthropic 兼容协议（可直接使用 Anthropic 消息格式）
    provider = DeepSeekAnthropicProvider.from_config(api_key="sk-...")

设计说明：
  - DeepSeekChatProvider：固定 base_url = "https://api.deepseek.com"，使用 ChatCompletionsAdapter + DeepSeekChatCodec
  - DeepSeekAnthropicProvider：固定 base_url = "https://api.deepseek.com/anthropic"，使用 AnthropicSDKAdapter + DeepSeekAnthropicCodec

DeepSeek Provider collection — supports Chat Completions and Anthropic-compatible protocols.
"""

from __future__ import annotations

from ccserver.model_engine.adapters.chat_completions import ChatCompletionsAdapter
from ccserver.model_engine.adapters.anthropic_sdk import AnthropicSDKAdapter
from ccserver.model_engine.codecs.deepseek_chat import DeepSeekChatCodec
from ccserver.model_engine.codecs.deepseek_anthropic import DeepSeekAnthropicCodec
from .base import BaseLLMProvider


# DeepSeek Chat Completions API 官方端点
# DeepSeek Chat Completions API official endpoint
_DEEPSEEK_CHAT_BASE_URL = "https://api.deepseek.com"

# DeepSeek Anthropic 兼容 API 端点
# DeepSeek Anthropic-compatible API endpoint
_DEEPSEEK_ANTHROPIC_BASE_URL = "https://api.deepseek.com/anthropic"


class DeepSeekChatProvider(BaseLLMProvider):
    """
    DeepSeek Chat Completions 协议 Provider。

    使用 ChatCompletionsAdapter + DeepSeekChatCodec，
    接入 DeepSeek 官方 Chat Completions API（OpenAI 兼容协议）。
    支持 deepseek-chat、deepseek-reasoner 等模型。

    DeepSeek Chat Completions Provider.
    Uses ChatCompletionsAdapter + DeepSeekChatCodec.
    Connects to DeepSeek's Chat Completions API (OpenAI-compatible).
    """

    @classmethod
    def from_config(
        cls,
        api_key: "str | None" = None,
    ) -> "DeepSeekChatProvider":
        """
        根据 api_key 创建实例。

        Args:
            api_key: DeepSeek API 密钥。None 时从环境变量 DEEPSEEK_API_KEY 读取（需调用方处理）。

        Returns:
            DeepSeekChatProvider — 已初始化的 provider 实例
        """
        adapter = ChatCompletionsAdapter.from_config(
            base_url=_DEEPSEEK_CHAT_BASE_URL,
            api_key=api_key,
        )
        codec = DeepSeekChatCodec()
        return cls(adapter=adapter, codec=codec)


class DeepSeekAnthropicProvider(BaseLLMProvider):
    """
    DeepSeek Anthropic 兼容接口 Provider。

    使用 AnthropicSDKAdapter + DeepSeekAnthropicCodec，
    接入 DeepSeek 的 Anthropic Messages API 兼容接口。
    允许使用 Anthropic 消息格式调用 DeepSeek 模型。

    DeepSeek Anthropic-compatible interface Provider.
    Uses AnthropicSDKAdapter + DeepSeekAnthropicCodec.
    Allows using Anthropic message format to call DeepSeek models.
    """

    @classmethod
    def from_config(
        cls,
        api_key: "str | None" = None,
    ) -> "DeepSeekAnthropicProvider":
        """
        根据 api_key 创建实例。

        Args:
            api_key: DeepSeek API 密钥。None 时 SDK 从环境变量读取。

        Returns:
            DeepSeekAnthropicProvider — 已初始化的 provider 实例
        """
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(
            base_url=_DEEPSEEK_ANTHROPIC_BASE_URL,
            api_key=api_key or "",
        )
        adapter = AnthropicSDKAdapter(client=client)
        codec = DeepSeekAnthropicCodec()
        return cls(adapter=adapter, codec=codec)
