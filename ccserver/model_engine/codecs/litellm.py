"""ccserver/model_engine/codecs/litellm.py

LiteLLMCodec — LiteLLM 统一代理编解码器（骨架）。

LiteLLM 作为代理服务时，暴露 OpenAI 兼容接口，
可直接复用 ChatCompletionsCodec 的所有逻辑。

如需 LiteLLM 特有逻辑（如 metadata、fallback 参数等），
在此子类中 override 对应方法。

LiteLLMCodec — LiteLLM unified proxy codec (skeleton).
Inherits ChatCompletionsCodec; override here for LiteLLM-specific behaviors.
"""

from __future__ import annotations

from .chat_completions import ChatCompletionsCodec


class LiteLLMCodec(ChatCompletionsCodec):
    """
    LiteLLM 统一代理编解码器（骨架）。

    继承 ChatCompletionsCodec，直接复用 OpenAI 兼容格式处理逻辑。
    LiteLLM-specific behaviors (e.g. metadata, fallback) can be added here if needed.
    """
