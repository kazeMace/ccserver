"""ccserver/model_engine/codecs/deepseek_anthropic.py

DeepSeekAnthropicCodec — DeepSeek Anthropic 兼容接口编解码器（骨架）。

DeepSeek 提供 Anthropic Messages API 兼容接口（/anthropic/v1/messages），
允许使用 Anthropic SDK 调用 DeepSeek 模型。

继承 AnthropicCodec，复用所有 Anthropic 格式处理逻辑。
当前为空骨架，无需 override（格式完全兼容）。

DeepSeekAnthropicCodec — DeepSeek Anthropic-compatible interface codec (skeleton).
Inherits AnthropicCodec directly; no overrides needed as the format is compatible.
"""

from __future__ import annotations

from .anthropic import AnthropicCodec


class DeepSeekAnthropicCodec(AnthropicCodec):
    """
    DeepSeek Anthropic 兼容接口编解码器（骨架）。

    直接继承 AnthropicCodec，复用所有 Anthropic 消息格式处理逻辑。
    Directly inherits AnthropicCodec; no additional logic needed.
    """
