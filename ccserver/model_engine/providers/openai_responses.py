"""ccserver/model_engine/providers/openai_responses.py

OpenAIResponsesProvider — OpenAI Responses API Provider（骨架）。

OpenAI Responses API 是新一代 API（区别于 Chat Completions），
支持内置工具（web search、code interpreter）和持久化上下文。

当前状态：骨架实现，from_config 中抛 NotImplementedError（等待 Adapter + Codec 实现后启用）。

OpenAIResponsesProvider — OpenAI Responses API Provider (skeleton).
Raises NotImplementedError in from_config until Adapter + Codec are implemented.
"""

from __future__ import annotations

from .base import BaseLLMProvider


class OpenAIResponsesProvider(BaseLLMProvider):
    """
    OpenAI Responses API Provider（骨架，待后续实现）。

    OpenAI Responses API Provider (skeleton, to be implemented later).
    Requires ResponsesAPIAdapter + ResponsesCodec implementations.
    """

    @classmethod
    def from_config(
        cls,
        base_url: "str | None" = None,
        api_key: "str | None" = None,
    ) -> "OpenAIResponsesProvider":
        """
        根据 base_url 和 api_key 创建实例（骨架，待实现）。

        Args:
            base_url: API 端点 URL。
            api_key:  API 密钥。

        Raises:
            NotImplementedError: ResponsesAPIAdapter + ResponsesCodec 尚未完整实现。
        """
        raise NotImplementedError(
            "OpenAIResponsesProvider 待实现：需要 ResponsesAPIAdapter + ResponsesCodec"
        )
