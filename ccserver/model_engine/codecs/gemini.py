"""ccserver/model_engine/codecs/gemini.py

GeminiCodec — Google Gemini Chat API 编解码器（骨架）。

Google Gemini 提供 OpenAI 兼容接口（通过 AI Studio 或 Vertex AI），
可通过 ChatCompletionsCodec 调用。

如需 Gemini 特有逻辑（如 grounding、multimodal 特殊格式等），
在此子类中 override 对应方法。

GeminiCodec — Google Gemini Chat codec (skeleton).
Inherits ChatCompletionsCodec; override here for Gemini-specific behaviors.
"""

from __future__ import annotations

from .chat_completions import ChatCompletionsCodec


class GeminiCodec(ChatCompletionsCodec):
    """
    Google Gemini Chat API 编解码器（骨架）。

    继承 ChatCompletionsCodec，复用 OpenAI 兼容格式处理逻辑。
    Gemini-specific behaviors can be added here if needed.
    """
