"""ccserver/model_engine/providers/litellm.py

LiteLLMProvider — LiteLLM 统一代理 Provider。

LiteLLM 官方文档：https://docs.litellm.ai/

使用方式：
    # LiteLLM 本地代理（默认端点）
    provider = LiteLLMProvider.from_config()

    # 自定义 LiteLLM 代理端点
    provider = LiteLLMProvider.from_config(base_url="http://litellm-proxy:4000/v1", api_key="sk-...")

设计说明：
  - 默认 base_url = "http://localhost:4000/v1"（LiteLLM 代理默认端口）
  - 使用 ChatCompletionsAdapter + LiteLLMCodec（OpenAI 兼容协议）

LiteLLMProvider — LiteLLM unified proxy Provider.
"""

from __future__ import annotations

from ccserver.model_engine.adapters.chat_completions import ChatCompletionsAdapter
from ccserver.model_engine.codecs.litellm import LiteLLMCodec
from .base import BaseLLMProvider


# LiteLLM 代理默认端点
# LiteLLM proxy default endpoint
_LITELLM_DEFAULT_BASE_URL = "http://localhost:4000/v1"


class LiteLLMProvider(BaseLLMProvider):
    """
    LiteLLM 统一代理 Provider。

    使用 ChatCompletionsAdapter + LiteLLMCodec，
    接入 LiteLLM 代理服务的 OpenAI 兼容 API。
    LiteLLM 可代理 100+ 个 LLM 服务。

    LiteLLM unified proxy Provider.
    Uses ChatCompletionsAdapter + LiteLLMCodec via LiteLLM's OpenAI-compatible API.
    """

    @classmethod
    def from_config(
        cls,
        base_url: "str | None" = None,
        api_key: "str | None" = None,
    ) -> "LiteLLMProvider":
        """
        根据 base_url 和 api_key 创建实例。

        Args:
            base_url: LiteLLM 代理端点。None 时使用默认本地端点（http://localhost:4000/v1）。
            api_key:  LiteLLM 代理 API 密钥（如配置了鉴权）。None 时使用空字符串。

        Returns:
            LiteLLMProvider — 已初始化的 provider 实例
        """
        adapter = ChatCompletionsAdapter.from_config(
            base_url=base_url or _LITELLM_DEFAULT_BASE_URL,
            api_key=api_key,
        )
        codec = LiteLLMCodec()
        return cls(adapter=adapter, codec=codec)
