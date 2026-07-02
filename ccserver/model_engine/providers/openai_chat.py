"""ccserver/model_engine/providers/openai_chat.py

OpenAIChatProvider — ChatCompletionsAdapter + ChatCompletionsCodec 组合。

使用方式：
    # 使用 OpenAI 官方端点
    provider = OpenAIChatProvider.from_config(api_key="sk-...")

    # 使用自定义端点（兼容 OpenAI 协议的第三方服务）
    provider = OpenAIChatProvider.from_config(base_url="http://...", api_key="...")

    # 非流式调用
    response = await provider.create(model="gpt-4o", messages=msgs, max_tokens=1024)

    # 流式调用
    async with provider.stream(model="gpt-4o", messages=msgs, max_tokens=1024) as ps:
        async for delta in ps:
            print(delta.text, end="", flush=True)
        response = await ps.get_final_response()

设计说明：
  - OpenAIChatProvider 只做组合（adapter + codec），不添加任何业务逻辑（SRP）
  - from_config() 工厂方法便于不同配置场景下创建实例

OpenAIChatProvider — combines ChatCompletionsAdapter + ChatCompletionsCodec.
"""

from __future__ import annotations

from ccserver.model_engine.adapters.chat_completions import ChatCompletionsAdapter
from ccserver.model_engine.codecs.chat_completions import ChatCompletionsCodec
from .base import BaseLLMProvider


class OpenAIChatProvider(BaseLLMProvider):
    """
    OpenAI Chat Completions Provider。

    组合 ChatCompletionsAdapter + ChatCompletionsCodec，
    继承 BaseLLMProvider 的 create/stream 实现。
    适用于 OpenAI 官方接口及所有兼容 OpenAI Chat Completions 协议的服务。

    OpenAI Chat Completions Provider.
    Combines ChatCompletionsAdapter + ChatCompletionsCodec.
    Works with OpenAI and all OpenAI-compatible APIs.
    """

    @classmethod
    def from_config(
        cls,
        base_url: "str | None" = None,
        api_key: "str | None" = None,
    ) -> "OpenAIChatProvider":
        """
        根据 base_url 和 api_key 创建实例。

        Args:
            base_url: API 端点 URL。None 时使用 OpenAI 官方端点。
            api_key:  API 密钥。None 时从环境变量 OPENAI_API_KEY 读取（由 SDK 处理）。

        Returns:
            OpenAIChatProvider — 已初始化的 provider 实例
        """
        adapter = ChatCompletionsAdapter.from_config(base_url=base_url, api_key=api_key)
        codec = ChatCompletionsCodec()
        return cls(adapter=adapter, codec=codec)
