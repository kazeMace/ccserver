"""ccserver/model_engine/providers/qwen.py

QwenProvider — Qwen（通义千问，阿里云）Chat API Provider。

阿里云百炼平台文档：https://help.aliyun.com/zh/model-studio/

使用方式：
    provider = QwenProvider.from_config(api_key="sk-...")

设计说明：
  - 固定 base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
  - 使用 ChatCompletionsAdapter + QwenCodec（OpenAI 兼容协议）

QwenProvider — Qwen (Alibaba Cloud DashScope) Chat Provider.
"""

from __future__ import annotations

from ccserver.model_engine.adapters.chat_completions import ChatCompletionsAdapter
from ccserver.model_engine.codecs.qwen import QwenCodec
from .base import BaseLLMProvider


# 阿里云百炼平台 OpenAI 兼容端点
# Alibaba Cloud DashScope OpenAI-compatible endpoint
_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class QwenProvider(BaseLLMProvider):
    """
    Qwen（通义千问）Chat API Provider。

    使用 ChatCompletionsAdapter + QwenCodec，
    接入阿里云百炼平台 Chat API（OpenAI 兼容协议）。

    Qwen (Alibaba Cloud DashScope) Chat Provider.
    Uses ChatCompletionsAdapter + QwenCodec.
    """

    @classmethod
    def from_config(
        cls,
        api_key: "str | None" = None,
    ) -> "QwenProvider":
        """
        根据 api_key 创建实例。

        Args:
            api_key: 阿里云百炼 API 密钥。None 时从环境变量读取（需调用方处理）。

        Returns:
            QwenProvider — 已初始化的 provider 实例
        """
        adapter = ChatCompletionsAdapter.from_config(
            base_url=_QWEN_BASE_URL,
            api_key=api_key,
        )
        codec = QwenCodec()
        return cls(adapter=adapter, codec=codec)
