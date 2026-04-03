"""
adapter — ModelAdapter 抽象基类。

所有 LLM 后端（Anthropic、OpenAI 兼容接口等）均实现此接口，
Agent 和 Compactor 只依赖此抽象，不直接引用具体 SDK。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ModelAdapter(ABC):
    """
    统一的 LLM 调用接口。

    子类需实现 create() 和 stream()，分别对应非流式和流式调用。
    返回值与 Anthropic SDK 的 Message / AsyncMessageStream 保持相同结构，
    调用方通过 response.content[i].text、response.stop_reason 等字段访问结果。
    """

    @abstractmethod
    async def create(
        self,
        *,
        model: str,
        messages: list[dict],
        max_tokens: int,
        system: list[dict] | str | None = None,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ):
        """
        非流式调用，返回完整的 Message 对象。
        等价于 anthropic_client.messages.create(...)
        """

    @abstractmethod
    def stream(
        self,
        *,
        model: str,
        messages: list[dict],
        max_tokens: int,
        system: list[dict] | str | None = None,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ):
        """
        流式调用，返回可用于 async with 的 context manager。
        等价于 anthropic_client.messages.stream(...)
        """
