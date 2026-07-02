"""ccserver/model_engine/providers/gemini.py

GeminiProvider — Google Gemini Chat API Provider。

Google AI Studio 文档：https://ai.google.dev/gemini-api/docs

使用方式：
    provider = GeminiProvider.from_config(api_key="AIza...")

设计说明：
  - 固定 base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
  - 使用 ChatCompletionsAdapter + GeminiCodec（OpenAI 兼容协议）

GeminiProvider — Google Gemini Chat Provider.
"""

from __future__ import annotations

from ccserver.model_engine.adapters.chat_completions import ChatCompletionsAdapter
from ccserver.model_engine.codecs.gemini import GeminiCodec
from .base import BaseLLMProvider


# Google AI Studio OpenAI 兼容端点
# Google AI Studio OpenAI-compatible endpoint
_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


class GeminiProvider(BaseLLMProvider):
    """
    Google Gemini Chat API Provider。

    使用 ChatCompletionsAdapter + GeminiCodec，
    接入 Google AI Studio 的 OpenAI 兼容 API。

    Google Gemini Chat Provider.
    Uses ChatCompletionsAdapter + GeminiCodec via Google AI Studio OpenAI-compatible API.
    """

    @classmethod
    def from_config(
        cls,
        api_key: "str | None" = None,
    ) -> "GeminiProvider":
        """
        根据 api_key 创建实例。

        Args:
            api_key: Google AI Studio API 密钥（以 "AIza" 开头）。
                     None 时从环境变量读取（需调用方处理）。

        Returns:
            GeminiProvider — 已初始化的 provider 实例
        """
        adapter = ChatCompletionsAdapter.from_config(
            base_url=_GEMINI_BASE_URL,
            api_key=api_key,
        )
        codec = GeminiCodec()
        return cls(adapter=adapter, codec=codec)
