"""ccserver/model_engine/adapters/ollama.py

OllamaAdapter — Ollama 本地模型服务适配器（骨架）。

Ollama 提供本地运行的开源模型服务（Llama、Mistral、Qwen 等）。
其 API 兼容 OpenAI Chat Completions（/v1/chat/completions），
但也有原生 API（/api/chat）。

当前状态：骨架实现，实际调用使用 ChatCompletionsAdapter（通过 OllamaProvider）。
Current state: skeleton. Actual calls use ChatCompletionsAdapter via OllamaProvider.
"""

from __future__ import annotations
from typing import Any

from .base import ProtocolAdapter


class OllamaAdapter(ProtocolAdapter):
    """
    Ollama 本地模型服务适配器（骨架，待后续实现）。

    Ollama local model service adapter (skeleton, to be implemented later).
    Raises NotImplementedError on call/stream until implemented.
    """

    async def call(self, **native_params: Any) -> Any:
        """
        非流式调用（骨架，待实现）。

        Args:
            **native_params: Ollama API 参数字典。

        Raises:
            NotImplementedError: 当前版本未实现。
        """
        raise NotImplementedError("OllamaAdapter 待实现")

    def stream(self, **native_params: Any) -> Any:
        """
        流式调用（骨架，待实现）。

        Args:
            **native_params: Ollama API 参数字典。

        Raises:
            NotImplementedError: 当前版本未实现。
        """
        raise NotImplementedError("OllamaAdapter 待实现")
