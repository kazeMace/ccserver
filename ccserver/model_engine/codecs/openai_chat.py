"""ccserver/model_engine/codecs/openai_chat.py

OpenAIChatCodec — OpenAI Chat Completions API 专用编解码器（骨架）。

继承 ChatCompletionsCodec，提供 OpenAI 官方 API 特有行为的扩展点。
当前为空骨架，父类的所有方法均可直接使用。

如需 OpenAI 特有逻辑（如 structured output、o1/o3 reasoning 参数等），
在此子类中 override 对应方法。

OpenAIChatCodec — OpenAI Chat Completions codec (skeleton).
Inherits ChatCompletionsCodec; override here for OpenAI-specific behaviors.
"""

from __future__ import annotations

from .chat_completions import ChatCompletionsCodec


class OpenAIChatCodec(ChatCompletionsCodec):
    """
    OpenAI Chat Completions API 专用编解码器（骨架）。

    当前直接继承 ChatCompletionsCodec，无额外逻辑。
    OpenAI-specific behaviors (e.g. structured output, o1/o3 reasoning) can be added here.
    """
