"""ccserver/model_engine/codecs/qwen.py

QwenCodec — Qwen（通义千问，阿里云）Chat API 编解码器（骨架）。

Qwen API 兼容 OpenAI Chat Completions 协议，
可直接复用 ChatCompletionsCodec 的所有逻辑。

如需 Qwen 特有逻辑（如特定思考链参数等），
在此子类中 override 对应方法。

QwenCodec — Qwen (Alibaba Cloud) Chat codec (skeleton).
Inherits ChatCompletionsCodec; override here for Qwen-specific behaviors.
"""

from __future__ import annotations

from .chat_completions import ChatCompletionsCodec


class QwenCodec(ChatCompletionsCodec):
    """
    Qwen（通义千问）Chat API 编解码器（骨架）。

    继承 ChatCompletionsCodec，直接复用 OpenAI 兼容格式处理逻辑。
    Qwen-specific behaviors can be added here if needed.
    """
