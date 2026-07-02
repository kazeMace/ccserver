"""ccserver/model_engine/adapters/responses_api.py

ResponsesAPIAdapter — OpenAI Responses API 适配器（骨架）。

OpenAI Responses API 是新一代 API（区别于 Chat Completions），
支持内置工具（web search、code interpreter 等）和持久化上下文。

当前状态：骨架实现，call/stream 待后续实现。
Current state: skeleton implementation, call/stream to be implemented later.
"""

from __future__ import annotations
from typing import Any

from .base import ProtocolAdapter


class ResponsesAPIAdapter(ProtocolAdapter):
    """
    OpenAI Responses API adapter（骨架，待后续实现）。

    OpenAI Responses API adapter (skeleton, to be implemented later).
    Raises NotImplementedError on call/stream until implemented.
    """

    async def call(self, **native_params: Any) -> Any:
        """
        非流式调用（骨架，待实现）。

        Args:
            **native_params: Responses API 参数字典。

        Raises:
            NotImplementedError: 当前版本未实现。
        """
        raise NotImplementedError("ResponsesAPIAdapter 待实现")

    def stream(self, **native_params: Any) -> Any:
        """
        流式调用（骨架，待实现）。

        Args:
            **native_params: Responses API 参数字典。

        Raises:
            NotImplementedError: 当前版本未实现。
        """
        raise NotImplementedError("ResponsesAPIAdapter 待实现")
