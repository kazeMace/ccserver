"""ccserver/model_engine/codecs/deepseek_chat.py

DeepSeekChatCodec — DeepSeek Chat API 编解码器（骨架）。

DeepSeek Chat API 兼容 OpenAI Chat Completions 协议，
但推理模型（deepseek-reasoner）会返回 reasoning_content 字段，
ChatCompletionsCodec 已处理此字段，无需额外 override。

如需 DeepSeek 特有逻辑（如特定系统提示格式、推理参数等），
在此子类中 override 对应方法。

DeepSeekChatCodec — DeepSeek Chat codec (skeleton).
Inherits ChatCompletionsCodec; ChatCompletionsCodec already handles reasoning_content.
"""

from __future__ import annotations

from .chat_completions import ChatCompletionsCodec


class DeepSeekChatCodec(ChatCompletionsCodec):
    """
    DeepSeek Chat API 编解码器（骨架）。

    继承 ChatCompletionsCodec，已支持 reasoning_content 字段（deepseek-reasoner）。
    DeepSeek-specific behaviors can be added here if needed.
    """
