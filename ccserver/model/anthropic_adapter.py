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
_vlm_adapter: AnthropicAdapter | None = None


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


def get_vlm_adapter() -> AnthropicAdapter:
    """
    返回 VLM 专用 adapter（进程级单例）。【已废弃】

    优先使用 CCSERVER_VLM_API_KEY / CCSERVER_VLM_BASE_URL 构造独立客户端，
    未配置时 fallback 到默认 adapter（ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL）。

    警告：此函数已废弃，请使用 VLMRouter 进行 VLM 路由决策。
    VLMRouter 支持多 provider 自动选择、fallback 链、模型能力感知等特性。
    """
    import warnings
    warnings.warn(
        "get_vlm_adapter() 已废弃，请使用 VLMRouter 进行 VLM 路由决策。"
        "VLMRouter 支持多 provider 自动选择（openai/qwen/zhipuai/anthropic 等）、"
        "fallback 链、模型能力感知等特性。",
        DeprecationWarning,
        stacklevel=2,
    )
    global _vlm_adapter
    if _vlm_adapter is None:
        from ccserver.config import VLM_API_KEY, VLM_BASE_URL
        if VLM_API_KEY or VLM_BASE_URL:
            kwargs: dict = {
                "http_client": httpx.AsyncClient(
                    timeout=httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=600.0),
                    limits=httpx.Limits(keepalive_expiry=5),
                )
            }
            if VLM_API_KEY:
                kwargs["api_key"] = VLM_API_KEY
            if VLM_BASE_URL:
                kwargs["base_url"] = VLM_BASE_URL
            _vlm_adapter = AnthropicAdapter(AsyncAnthropic(**kwargs))
        else:
            _vlm_adapter = get_default_adapter()
    return _vlm_adapter
