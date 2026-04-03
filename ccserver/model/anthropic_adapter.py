"""
anthropic_adapter — 基于 Anthropic SDK 的 ModelAdapter 实现。

get_default_adapter() 返回进程级单例，所有会话共享同一个 AsyncAnthropic 实例。
keepalive_expiry=5：空闲连接最多保留 5 秒，超时即丢弃，
防止长耗时操作（如 MCP 调用）后复用已被服务端关闭的连接，
避免 "incomplete chunked read" 类错误。
"""

from __future__ import annotations

from typing import Any

import httpx
from anthropic import AsyncAnthropic

from .adapter import ModelAdapter


class AnthropicAdapter(ModelAdapter):
    """封装 AsyncAnthropic 客户端，实现 ModelAdapter 接口。"""

    def __init__(self, client: AsyncAnthropic):
        self._client = client

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
        params: dict[str, Any] = dict(model=model, messages=messages, max_tokens=max_tokens, **kwargs)
        if system is not None:
            params["system"] = system
        if tools is not None:
            params["tools"] = tools
        return await self._client.messages.create(**params)

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
        params: dict[str, Any] = dict(model=model, messages=messages, max_tokens=max_tokens, **kwargs)
        if system is not None:
            params["system"] = system
        if tools is not None:
            params["tools"] = tools
        return self._client.messages.stream(**params)


# ── 进程级单例 ────────────────────────────────────────────────────────────────

_default_adapter: AnthropicAdapter | None = None


def get_default_adapter() -> AnthropicAdapter:
    global _default_adapter
    if _default_adapter is None:
        client = AsyncAnthropic(
            http_client=httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=600.0),
                limits=httpx.Limits(keepalive_expiry=5),
            )
        )
        _default_adapter = AnthropicAdapter(client)
    return _default_adapter
