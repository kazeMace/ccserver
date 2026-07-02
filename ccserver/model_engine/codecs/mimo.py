"""ccserver/model_engine/codecs/mimo.py

MimoCodec — MiMo（小米）Chat API 编解码器（骨架）。

MiMo API 兼容 OpenAI Chat Completions 协议，
可直接复用 ChatCompletionsCodec 的所有逻辑。

如需 MiMo 特有逻辑（如特定推理参数等），
在此子类中 override 对应方法。

MimoCodec — MiMo (Xiaomi) Chat codec (skeleton).
Inherits ChatCompletionsCodec; override here for MiMo-specific behaviors.
"""

from __future__ import annotations

from .chat_completions import ChatCompletionsCodec


class MimoCodec(ChatCompletionsCodec):
    """
    MiMo（小米）Chat API 编解码器（骨架）。

    继承 ChatCompletionsCodec，直接复用 OpenAI 兼容格式处理逻辑。
    MiMo-specific behaviors can be added here if needed.
    """
