"""tests/test_provider_factory.py — AdapterFactory 路由测试。

测试 (provider_id, api_type) → LLMProvider 路由逻辑：
  - 精确匹配（anthropic、openai）
  - 回退到兼容 Provider（provider_id="" + openai-completions）
  - 未知 (provider_id, api_type) 组合抛 ValueError

Test (provider_id, api_type) -> LLMProvider routing logic:
  - Exact match (anthropic, openai)
  - Fallback to compatible provider (provider_id="" + openai-completions)
  - Unknown (provider_id, api_type) combination raises ValueError
"""

from unittest.mock import patch, MagicMock

import pytest

from ccserver.model_engine.wiring.factory import AdapterFactory
from ccserver.model_engine.wiring.endpoint import ModelEndpoint
from ccserver.model_engine.providers.base import LLMProvider


def test_build_anthropic_provider():
    """精确匹配 anthropic + anthropic-messages → AnthropicProvider。"""
    ep = ModelEndpoint(
        model_id="claude-sonnet-4-6",
        api_type="anthropic-messages",
        provider="anthropic",
        base_url=None,
        api_key="sk-test",
    )
    with patch("anthropic.AsyncAnthropic") as mock_cls:
        mock_cls.return_value = MagicMock()
        provider = AdapterFactory.build(ep)

    assert isinstance(provider, LLMProvider)


def test_build_openai_chat_provider():
    """精确匹配 openai + openai-completions → OpenAIChatProvider。"""
    ep = ModelEndpoint(
        model_id="gpt-4o",
        api_type="openai-completions",
        provider="openai",
        base_url=None,
        api_key="sk-test",
    )
    from ccserver.model_engine.providers.openai_chat import OpenAIChatProvider
    with patch("ccserver.model_engine.adapters.chat_completions.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = MagicMock()
        provider = AdapterFactory.build(ep)

    assert isinstance(provider, OpenAIChatProvider)


def test_build_unknown_fallback_to_compatible():
    """未知 provider + openai-completions 回退到 CompatibleOpenAIProvider。"""
    ep = ModelEndpoint(
        model_id="some-model",
        api_type="openai-completions",
        provider="",
        base_url="http://custom",
        api_key="sk-test",
    )
    from ccserver.model_engine.providers.compatible import CompatibleOpenAIProvider
    with patch("ccserver.model_engine.adapters.chat_completions.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = MagicMock()
        provider = AdapterFactory.build(ep)

    assert isinstance(provider, CompatibleOpenAIProvider)


def test_build_unknown_provider_id_falls_back_not_raises():
    """
    未知 provider_id（非空）+ openai-completions 也应回退到兼容 Provider，不抛 ValueError。

    原因：回退规则是 ("", api_type)，任何未注册的 provider_id 都走这条路。
    """
    ep = ModelEndpoint(
        model_id="unknown-model",
        api_type="openai-completions",
        provider="unknown-vendor",
        base_url="http://unknown",
        api_key="sk-test",
    )
    from ccserver.model_engine.providers.compatible import CompatibleOpenAIProvider
    with patch("ccserver.model_engine.adapters.chat_completions.AsyncOpenAI") as mock_cls:
        mock_cls.return_value = MagicMock()
        provider = AdapterFactory.build(ep)

    assert isinstance(provider, CompatibleOpenAIProvider)


def test_build_raises_for_unknown_api_type():
    """未知 api_type（且无回退）时抛 ValueError。"""
    ep = ModelEndpoint(
        model_id="some-model",
        api_type="totally-unknown-protocol",
        provider="",
        base_url=None,
        api_key=None,
    )
    with pytest.raises(ValueError, match="未知 provider/api_type 组合"):
        AdapterFactory.build(ep)
