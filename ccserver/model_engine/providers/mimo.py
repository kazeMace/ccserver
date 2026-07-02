"""ccserver/model_engine/providers/mimo.py

MimoProvider — MiMo（小米）Chat API Provider。

MiMo 官方文档：https://developer.miui.com/api/mimo

使用方式：
    provider = MimoProvider.from_config(api_key="sk-...", base_url="https://...")

设计说明：
  - 使用 ChatCompletionsAdapter + MimoCodec（OpenAI 兼容协议）
  - base_url 需由调用方传入（MiMo 服务端点可能因部署方式不同而变化）

MimoProvider — MiMo (Xiaomi) Chat Provider.
"""

from __future__ import annotations

from ccserver.model_engine.adapters.chat_completions import ChatCompletionsAdapter
from ccserver.model_engine.codecs.mimo import MimoCodec
from .base import BaseLLMProvider


class MimoProvider(BaseLLMProvider):
    """
    MiMo（小米）Chat API Provider。

    使用 ChatCompletionsAdapter + MimoCodec，
    接入 MiMo Chat API（OpenAI 兼容协议）。

    MiMo (Xiaomi) Chat Provider.
    Uses ChatCompletionsAdapter + MimoCodec.
    """

    @classmethod
    def from_config(
        cls,
        base_url: "str | None" = None,
        api_key: "str | None" = None,
    ) -> "MimoProvider":
        """
        根据 base_url 和 api_key 创建实例。

        Args:
            base_url: MiMo API 端点 URL。
            api_key:  MiMo API 密钥。None 时从环境变量读取（需调用方处理）。

        Returns:
            MimoProvider — 已初始化的 provider 实例
        """
        adapter = ChatCompletionsAdapter.from_config(
            base_url=base_url,
            api_key=api_key,
        )
        codec = MimoCodec()
        return cls(adapter=adapter, codec=codec)
