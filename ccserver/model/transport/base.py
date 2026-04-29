"""
base — TransportProtocol 抽象基类。

将"传输协议"（HTTP/SDK 调用方式）从 Provider 中解耦。
多个 provider 可以共享同一个 transport 实现（如所有 OpenAI 兼容 provider 共享 OpenAICompatTransport）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class TransportProtocol(ABC):
    """
    传输协议抽象。

    描述如何与远端 API 通信：构建请求、发送请求、解析响应。
    ModelAdapter 实例内部使用一个 TransportProtocol 完成实际的网络调用。

    子类需实现 create()（非流式）和 stream()（流式）。
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
        非流式调用，返回类 Anthropic Message 对象。

        Args:
            model:      模型名
            messages:   Anthropic 格式的消息列表
            max_tokens: 最大输出 token 数
            system:     system prompt
            tools:      工具定义列表（Anthropic 格式）
            **kwargs:   额外参数（如 thinking、temperature 等）

        Returns:
            类 Anthropic Message 对象（包含 .content 列表和 .stop_reason）
        """
        ...

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
        流式调用，返回 async context manager。

        Returns:
            可用于 async with 的 context manager
        """
        ...
