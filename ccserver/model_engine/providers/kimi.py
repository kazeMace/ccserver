"""ccserver/model_engine/providers/kimi.py

KimiProvider — Kimi（月之暗面）Chat API Provider。

Kimi 官方文档：https://platform.moonshot.cn/docs/api/chat

使用方式：
    provider = KimiProvider.from_config(api_key="sk-...")

设计说明：
  - 固定 base_url = "https://api.moonshot.cn/v1"
  - 使用 ChatCompletionsAdapter + KimiCodec（OpenAI 兼容协议）

KimiProvider — Kimi (Moonshot AI) Chat Provider.
"""

from __future__ import annotations

from ccserver.model_engine.adapters.chat_completions import ChatCompletionsAdapter
from ccserver.model_engine.codecs.kimi import KimiCodec
from .base import BaseLLMProvider


# Kimi Chat API 官方端点
# Kimi Chat API official endpoint
_KIMI_BASE_URL = "https://api.moonshot.cn/v1"


class KimiProvider(BaseLLMProvider):
    """
    Kimi（月之暗面）Chat API Provider。

    使用 ChatCompletionsAdapter + KimiCodec，
    接入 Kimi 官方 Chat API（OpenAI 兼容协议）。

    Kimi (Moonshot AI) Chat Provider.
    Uses ChatCompletionsAdapter + KimiCodec.
    """

    @classmethod
    def from_config(
        cls,
        api_key: "str | None" = None,
    ) -> "KimiProvider":
        """
        根据 api_key 创建实例。

        Args:
            api_key: Kimi API 密钥。None 时从环境变量读取（需调用方处理）。

        Returns:
            KimiProvider — 已初始化的 provider 实例
        """
        adapter = ChatCompletionsAdapter.from_config(
            base_url=_KIMI_BASE_URL,
            api_key=api_key,
        )
        codec = KimiCodec()
        return cls(adapter=adapter, codec=codec)
