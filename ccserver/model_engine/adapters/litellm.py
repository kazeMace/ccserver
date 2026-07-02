"""ccserver/model_engine/adapters/litellm.py

LiteLLMAdapter — LiteLLM 统一代理适配器（骨架）。

LiteLLM 提供统一接口调用 100+ LLM 服务（OpenAI、Anthropic、Google、AWS 等），
可作为本地代理（proxy）或直接调用（SDK）。

当前状态：骨架实现，call/stream 待后续实现。
Current state: skeleton implementation, call/stream to be implemented later.
"""

from __future__ import annotations
from typing import Any

from .base import ProtocolAdapter


class LiteLLMAdapter(ProtocolAdapter):
    """
    LiteLLM 统一代理适配器（骨架，待后续实现）。

    LiteLLM unified proxy adapter (skeleton, to be implemented later).
    Raises NotImplementedError on call/stream until implemented.
    """

    async def call(self, **native_params: Any) -> Any:
        """
        非流式调用（骨架，待实现）。

        Args:
            **native_params: LiteLLM API 参数字典。

        Raises:
            NotImplementedError: 当前版本未实现。
        """
        raise NotImplementedError("LiteLLMAdapter 待实现")

    def stream(self, **native_params: Any) -> Any:
        """
        流式调用（骨架，待实现）。

        Args:
            **native_params: LiteLLM API 参数字典。

        Raises:
            NotImplementedError: 当前版本未实现。
        """
        raise NotImplementedError("LiteLLMAdapter 待实现")
