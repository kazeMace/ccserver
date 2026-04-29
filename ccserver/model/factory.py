"""
factory — 运行时 ModelAdapter 选择工厂。

根据 provider 名称委托给 ProviderRegistry 创建对应的 ModelAdapter。
保持向后兼容：所有现有 get_adapter() 调用签名不变。
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from .adapter import ModelAdapter
from .plugins.registry import get_provider_registry


def get_adapter(provider: str | None = None, **config: Any) -> ModelAdapter:
    """
    根据 provider 名称返回对应的 ModelAdapter 实例。

    委托给 ProviderRegistry 进行创建，ProviderRegistry 通过 Plugin 系统
    支持动态注册新的 provider。

    Args:
        provider: 提供商名称，如 "anthropic"、"openai"、"qwen"、"zhipuai" 等。
                  为 None 时默认返回 anthropic。
        **config: 额外的配置参数，如 generic provider 需要的 base_url/api_key。

    Returns:
        ModelAdapter 实例。

    Raises:
        ValueError: 未知的 provider 名称。

    Usage:
        # 默认 Anthropic
        adapter = get_adapter()

        # 指定 provider
        adapter = get_adapter("openai")

        # 通义千问
        adapter = get_adapter("qwen")

        # 智谱 GLM
        adapter = get_adapter("zhipuai")

        # 通用 OpenAI 兼容端点
        adapter = get_adapter("generic", base_url="http://localhost:8080/v1", api_key="sk-xxx")
    """
    provider = (provider or "anthropic").lower()
    registry = get_provider_registry()

    try:
        adapter = registry.create_adapter(provider, **config)
        logger.debug("Adapter created | provider={}", provider)
        return adapter
    except ValueError:
        # registry.create_adapter 内部已记录详细错误
        raise
