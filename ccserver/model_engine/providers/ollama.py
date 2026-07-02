"""ccserver/model_engine/providers/ollama.py

OllamaProvider — Ollama 本地模型服务 Provider。

Ollama 官方文档：https://ollama.com/

使用方式：
    # 默认本地端点（http://localhost:11434）
    provider = OllamaProvider.from_config()

    # 自定义端点
    provider = OllamaProvider.from_config(base_url="http://remote-host:11434/v1")

设计说明：
  - 默认 base_url = "http://localhost:11434/v1"（Ollama OpenAI 兼容接口路径）
  - 使用 ChatCompletionsAdapter + OllamaCodec（OpenAI 兼容协议）
  - Ollama 本地服务通常无需 api_key

OllamaProvider — Ollama local model service Provider.
"""

from __future__ import annotations

from ccserver.model_engine.adapters.chat_completions import ChatCompletionsAdapter
from ccserver.model_engine.codecs.ollama import OllamaCodec
from .base import BaseLLMProvider


# Ollama 默认本地端点（OpenAI 兼容接口路径）
# Ollama default local endpoint (OpenAI-compatible path)
_OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434/v1"


class OllamaProvider(BaseLLMProvider):
    """
    Ollama 本地模型服务 Provider。

    使用 ChatCompletionsAdapter + OllamaCodec，
    接入 Ollama 的 OpenAI 兼容 API（/v1/chat/completions）。
    支持 llama、mistral、qwen 等所有 Ollama 托管的开源模型。

    Ollama local model service Provider.
    Uses ChatCompletionsAdapter + OllamaCodec via Ollama's OpenAI-compatible API.
    """

    @classmethod
    def from_config(
        cls,
        base_url: "str | None" = None,
        api_key: "str | None" = None,
    ) -> "OllamaProvider":
        """
        根据 base_url 创建实例。

        Args:
            base_url: Ollama 服务端点。None 时使用默认本地端点（http://localhost:11434/v1）。
            api_key:  通常为 None（Ollama 本地服务不需要认证）。

        Returns:
            OllamaProvider — 已初始化的 provider 实例
        """
        adapter = ChatCompletionsAdapter.from_config(
            base_url=base_url or _OLLAMA_DEFAULT_BASE_URL,
            api_key=api_key or "ollama",  # Ollama 需要非空字符串，但值无意义
        )
        codec = OllamaCodec()
        return cls(adapter=adapter, codec=codec)
