"""
factory — 运行时 ModelAdapter 选择工厂。

根据配置（settings.json 或环境变量）选择对应的 LLM 后端：
  anthropic、openai、openrouter、ollama、lmstudio、oneapi、volcano、generic
"""

from __future__ import annotations

import os
from typing import Any

from loguru import logger

from .adapter import ModelAdapter
from .anthropic_adapter import get_default_adapter as get_anthropic_default
from .openai_adapter import OpenAIAdapter
from .volcano_adapter import VolcanoAdapter


_PROVIDER_BUILDERS = {
    "anthropic": lambda cfg: get_anthropic_default(),
    "openai": lambda cfg: OpenAIAdapter.from_env(),
    "openrouter": lambda cfg: OpenAIAdapter.from_config(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.getenv("OPENROUTER_API_KEY", ""),
    ),
    "ollama": lambda cfg: OpenAIAdapter.from_config(
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        api_key="ollama",
    ),
    "lmstudio": lambda cfg: OpenAIAdapter.from_config(
        base_url=os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1"),
        api_key="",
    ),
    "oneapi": lambda cfg: OpenAIAdapter.from_config(
        base_url=os.getenv("ONEAPI_BASE_URL", ""),
        api_key=os.getenv("ONEAPI_API_KEY", ""),
    ),
    "volcano": lambda cfg: VolcanoAdapter.from_env(),
    "generic": lambda cfg: OpenAIAdapter.from_config(
        base_url=cfg.get("base_url", ""),
        api_key=cfg.get("api_key", ""),
    ),
}


def get_adapter(provider: str | None = None, **config: Any) -> ModelAdapter:
    """
    根据 provider 名称返回对应的 ModelAdapter 实例。

    Args:
        provider: 提供商名称，如 "anthropic"、"openai"、"ollama" 等。
                  为 None 时默认返回 anthropic。
        **config: 额外的配置参数，如 generic provider 需要的 base_url/api_key。

    Returns:
        ModelAdapter 实例。

    Raises:
        ValueError: 未知的 provider 名称。
    """
    provider = (provider or "anthropic").lower()
    builder = _PROVIDER_BUILDERS.get(provider)
    if builder is None:
        raise ValueError(f"Unknown provider: {provider!r}. Supported: {list(_PROVIDER_BUILDERS.keys())}")

    adapter = builder(config)
    logger.debug("Adapter created | provider={}", provider)
    return adapter
