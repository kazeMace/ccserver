"""ccserver/model_engine/providers/anthropic.py

AnthropicProvider — AnthropicSDKAdapter + AnthropicCodec 的组合。

使用方式：
    # 从现有 client 创建（测试友好）
    provider = AnthropicProvider.from_client(client)

    # 使用进程级单例（生产环境）
    provider = get_default_provider()

    # 非流式调用
    response = await provider.create(model="claude-...", messages=msgs, max_tokens=1024)

    # 流式调用
    async with provider.stream(model="claude-...", messages=msgs, max_tokens=1024) as ps:
        async for delta in ps:
            print(delta.text, end="", flush=True)
        response = await ps.get_final_response()

设计说明：
  - AnthropicProvider 只做组合（adapter + codec），不添加任何业务逻辑（SRP）
  - from_client() 工厂方法便于测试（注入 mock client）
  - 进程级单例 _default_provider 避免重复创建 httpx 连接池
"""

from __future__ import annotations

from loguru import logger

from ccserver.model_engine.adapters.anthropic_sdk import AnthropicSDKAdapter
from ccserver.model_engine.codecs.anthropic import AnthropicCodec
from .base import BaseLLMProvider


class AnthropicProvider(BaseLLMProvider):
    """
    Anthropic Messages API Provider。

    组合 AnthropicSDKAdapter + AnthropicCodec，继承 BaseLLMProvider 的 create/stream 实现。
    通常无需 override 任何方法，只通过构造器注入不同 adapter/codec 来定制行为。

    Anthropic Messages API Provider.
    Combines AnthropicSDKAdapter + AnthropicCodec; inherits BaseLLMProvider's create/stream.
    """

    @classmethod
    def from_client(cls, client) -> "AnthropicProvider":
        """
        从 AsyncAnthropic client 创建 AnthropicProvider 实例。

        便于测试（传入 mock client）和自定义连接配置（传入带自定义配置的 client）。

        Args:
            client: anthropic.AsyncAnthropic 实例（或兼容的 mock 对象）

        Returns:
            AnthropicProvider — 已初始化的 provider 实例
        """
        assert client is not None, "AnthropicProvider.from_client: client 不能为 None"
        adapter = AnthropicSDKAdapter(client=client)
        codec = AnthropicCodec()
        logger.debug("AnthropicProvider.from_client: 创建实例")
        return cls(adapter=adapter, codec=codec)


# ── 进程级单例 ────────────────────────────────────────────────────────────────
# Process-level singleton: avoids recreating httpx connection pool on every call

_default_provider: "AnthropicProvider | None" = None


def get_default_provider() -> AnthropicProvider:
    """
    获取进程级 AnthropicProvider 单例。

    首次调用时，从环境变量读取 API key（ANTHROPIC_API_KEY），
    使用共享 httpx.AsyncClient（keepalive_expiry=5s）创建 AsyncAnthropic client。
    后续调用直接返回缓存实例，不重新创建连接池。

    Returns:
        AnthropicProvider — 单例实例

    Note:
        需要环境变量 ANTHROPIC_API_KEY（或 Anthropic SDK 支持的其他认证方式）。

    Get the process-level AnthropicProvider singleton.
    First call reads ANTHROPIC_API_KEY from env and creates the httpx connection pool.
    Subsequent calls return the cached instance.
    """
    global _default_provider
    if _default_provider is None:
        from anthropic import AsyncAnthropic
        from ccserver.model_engine.wiring.http import make_async_http_client
        logger.info("get_default_provider: 初始化 AnthropicProvider 单例")
        client = AsyncAnthropic(http_client=make_async_http_client())
        _default_provider = AnthropicProvider.from_client(client)
    return _default_provider
