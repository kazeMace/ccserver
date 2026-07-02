"""ccserver/model_engine/codecs/kimi.py

KimiCodec — Kimi（月之暗面）Chat API 编解码器（骨架）。

Kimi API 兼容 OpenAI Chat Completions 协议，
可直接复用 ChatCompletionsCodec 的所有逻辑。

如需 Kimi 特有逻辑（如长文档上下文处理、特定参数等），
在此子类中 override 对应方法。

KimiCodec — Kimi (Moonshot AI) Chat codec (skeleton).
Inherits ChatCompletionsCodec; override here for Kimi-specific behaviors.
"""

from __future__ import annotations

from .chat_completions import ChatCompletionsCodec


class KimiCodec(ChatCompletionsCodec):
    """
    Kimi（月之暗面）Chat API 编解码器（骨架）。

    继承 ChatCompletionsCodec，直接复用 OpenAI 兼容格式处理逻辑。
    Kimi-specific behaviors can be added here if needed.
    """
