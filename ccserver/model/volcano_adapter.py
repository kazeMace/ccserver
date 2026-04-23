"""
volcano_adapter — 火山方舟 (volcengine-python-sdk[ark]) 专用 ModelAdapter 实现。

虽然火山方舟 API 与 OpenAI 类似，但存在以下差异：
  1. SDK 类名不同：volcenginesdkarkruntime.Ark
  2. base_url 固定为 https://ark.cn-beijing.volces.com/api/v3
  3. 认证方式和模型 ID 格式有差异

因此独立封装，不直接复用 OpenAIAdapter。
"""

from __future__ import annotations

from typing import Any

import httpx

from .adapter import ModelAdapter
from .translator import (
    anthropic_to_openai_messages,
    anthropic_to_openai_tools,
    openai_to_anthropic_message,
)

try:
    from volcenginesdkarkruntime import Ark
except ImportError:
    Ark = None  # type: ignore


class VolcanoAdapter(ModelAdapter):
    """封装火山方舟 Ark 客户端，实现 ModelAdapter 接口。"""

    def __init__(self, client):
        """
        初始化 VolcanoAdapter。

        Args:
            client: volcenginesdkarkruntime.Ark 实例。
        """
        assert client is not None, "Ark client must not be None"
        self._client = client

    @classmethod
    def from_env(cls) -> "VolcanoAdapter":
        """从 ARK_API_KEY 环境变量创建默认实例。"""
        assert Ark is not None, "volcenginesdkarkruntime package is not installed"
        import os
        api_key = os.getenv("ARK_API_KEY", "")
        base_url = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
        client = Ark(
            api_key=api_key,
            base_url=base_url,
            http_client=httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=600.0),
                limits=httpx.Limits(keepalive_expiry=5),
            ),
        )
        return cls(client)

    @classmethod
    def from_config(cls, api_key: str | None = None, base_url: str | None = None) -> "VolcanoAdapter":
        """根据 api_key 和 base_url 创建实例。"""
        assert Ark is not None, "volcenginesdkarkruntime package is not installed"
        import os
        client = Ark(
            api_key=api_key or os.getenv("ARK_API_KEY", ""),
            base_url=base_url or os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
            http_client=httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=600.0),
                limits=httpx.Limits(keepalive_expiry=5),
            ),
        )
        return cls(client)

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
        """非流式调用，返回模拟 Anthropic Message 的对象。"""
        openai_messages = anthropic_to_openai_messages(messages, system)
        openai_tools = anthropic_to_openai_tools(tools)

        params: dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
        }
        if openai_tools is not None:
            params["tools"] = openai_tools
        if kwargs:
            params.update(kwargs)

        response = await self._client.chat.completions.create(**params)
        return openai_to_anthropic_message(response)

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
        """流式调用。"""
        # 火山 Ark 的流式接口与 OpenAI 兼容，复用 OpenAIStreamWrapper
        from .openai_adapter import OpenAIStreamWrapper

        openai_messages = anthropic_to_openai_messages(messages, system)
        openai_tools = anthropic_to_openai_tools(tools)

        params: dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if openai_tools is not None:
            params["tools"] = openai_tools
        if kwargs:
            params.update(kwargs)

        return OpenAIStreamWrapper(self._client.chat.completions.create(**params))
