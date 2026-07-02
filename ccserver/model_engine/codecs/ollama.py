"""ccserver/model_engine/codecs/ollama.py

OllamaCodec — Ollama 本地模型服务编解码器（骨架）。

Ollama 的 OpenAI 兼容接口（/v1/chat/completions）与标准 Chat Completions 一致，
可直接复用 ChatCompletionsCodec 的所有逻辑。

如需 Ollama 原生 API（/api/chat）特有逻辑（如 options 参数等），
在此子类中 override 对应方法。

OllamaCodec — Ollama local model service codec (skeleton).
Inherits ChatCompletionsCodec; override here for Ollama-specific behaviors.
"""

from __future__ import annotations

from .chat_completions import ChatCompletionsCodec


class OllamaCodec(ChatCompletionsCodec):
    """
    Ollama 本地模型服务编解码器（骨架）。

    继承 ChatCompletionsCodec，直接复用 OpenAI 兼容格式处理逻辑。
    Ollama-specific behaviors (e.g. options, keep_alive) can be added here if needed.
    """
