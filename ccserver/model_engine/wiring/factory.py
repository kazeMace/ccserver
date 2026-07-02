"""
factory — 运行时 LLMProvider 选择工厂（重写）。

路由规则：(provider_id, api_type) → builder 函数。
provider_id 为 "" 时回退到兼容 Provider。

设计意图：
  旧版本只按 api_type 路由（_API_TYPE_BUILDERS），导致无法区分同一协议下的不同 provider
  （如 deepseek-openai-compat vs openai-openai-compat）。
  新版本用 (provider_id, api_type) 二维键路由，精确匹配；未知 provider 自动回退到
  通用兼容 Provider（provider_id=""）。

  Old version routed only by api_type; new version routes by (provider_id, api_type),
  falling back to generic compatible provider when provider is unknown.
"""

from __future__ import annotations

from typing import Callable, Any

from loguru import logger

from .endpoint import ModelEndpoint, API_TYPE_ANTHROPIC, API_TYPE_OPENAI
from .providers import PROVIDER_SPEC_BY_ID, API_TYPE_RESPONSES, API_TYPE_OLLAMA, API_TYPE_LITELLM


# ── 路由表 ─────────────────────────────────────────────────────────────────────
# 键：(provider_id, api_type)，值：builder(endpoint) → LLMProvider
# Key: (provider_id, api_type), Value: builder(endpoint) -> LLMProvider
ProviderBuilder = Callable[[ModelEndpoint], Any]
_PROVIDER_BUILDERS: dict[tuple[str, str], ProviderBuilder] = {}


def register_provider_builder(provider_id: str, api_type: str):
    """
    装饰器：注册 (provider_id, api_type) → builder 映射。

    Args:
        provider_id: provider 唯一标识，如 "anthropic"、"openai"；
                     "" 表示通用兼容 provider（作为回退路由）。
        api_type:    协议类型，如 API_TYPE_ANTHROPIC、API_TYPE_OPENAI。

    Returns:
        装饰器；被装饰函数原样返回（注册副作用发生在模块加载时）。

    Raises:
        AssertionError: 同一 (provider_id, api_type) 被重复注册。

    Decorator: register (provider_id, api_type) -> builder mapping.
    """
    def _decorator(builder: ProviderBuilder) -> ProviderBuilder:
        key = (provider_id, api_type)
        assert key not in _PROVIDER_BUILDERS, (
            f"重复注册: {key!r}，已被 {_PROVIDER_BUILDERS[key].__name__} 占用"
        )
        _PROVIDER_BUILDERS[key] = builder
        return builder

    return _decorator


class AdapterFactory:
    """
    根据 ModelEndpoint 构造 LLMProvider，并注入 model_info 和 compatibility。

    这是构造 LLMProvider 的唯一入口。
    根据 (endpoint.provider, endpoint.api_type) 选择对应的 Provider 实现。

    The sole entry point for constructing LLMProvider instances.
    Routes by (endpoint.provider, endpoint.api_type).
    """

    @staticmethod
    def build(endpoint: ModelEndpoint):
        """
        根据 ModelEndpoint 构造对应的 LLMProvider。

        步骤：
          1. endpoint.resolve() — 补全所有 None 字段
          2. _build_provider()  — 按路由表选择 Provider 实现
          3. 从 ModelInfoRegistry 查询并注入 model_info
          4. 从 CompatibilityRegistry 查询并注入 compatibility

        Args:
            endpoint: ModelEndpoint 实例（允许字段不完整，内部 resolve()）

        Returns:
            注入了 model_info 和 compatibility 的 LLMProvider 实例

        Raises:
            AssertionError: endpoint 为 None 或 model_id 为空
            ValueError:     (provider_id, api_type) 组合未在路由表中找到

        Build LLMProvider from ModelEndpoint; injects model_info + compatibility.
        """
        assert endpoint is not None and endpoint.model_id, "endpoint.model_id 不能为空"

        # Step 1: 补全所有 None 字段
        ep = endpoint.resolve()

        logger.debug(
            "AdapterFactory.build | model_id={} provider={} api_type={}",
            ep.model_id, ep.provider, ep.api_type,
        )

        # Step 2: 按 (provider_id, api_type) 路由，构造 Provider 实例
        provider = _build_provider(ep)

        # Step 3: 注入 model_info（从 ModelInfoRegistry 查询，未知模型返回 None）
        from ..metadata.model_info_registry import get_registry
        provider.model_info = get_registry().get(ep.model_id)

        # Step 4: 注入 compatibility（从 CompatibilityRegistry 查询，带优先级匹配）
        from ..metadata.compatibility_registry import get_compatibility_registry
        provider.compatibility = get_compatibility_registry().get(ep.model_id, ep.api_type)

        logger.info(
            "AdapterFactory.build 完成 | model_id={} api_type={} "
            "supports_image={} supports_tools={}",
            ep.model_id, ep.api_type,
            provider.supports_image, provider.supports_tools,
        )

        return provider


def _build_provider(ep: ModelEndpoint):
    """
    按 (provider_id, api_type) 路由，构造底层 LLMProvider 实例。

    匹配规则（优先级从高到低）：
      1. 精确匹配：(provider_id, api_type)
      2. 回退匹配：("", api_type) — 通用兼容 Provider

    Args:
        ep: 已 resolve() 的 ModelEndpoint（api_type 一定非 None）

    Returns:
        未注入 model_info/compatibility 的 LLMProvider 实例

    Raises:
        ValueError: 精确键和回退键均未命中路由表

    Routes by (provider_id, api_type) to construct the LLMProvider instance.
    """
    api_type    = ep.api_type or API_TYPE_OPENAI
    provider_id = ep.provider or ""

    # 1. 精确匹配
    key = (provider_id, api_type)
    builder = _PROVIDER_BUILDERS.get(key)
    if builder:
        return builder(ep)

    # 2. 回退到通用兼容 Provider（provider_id=""）
    fallback_key = ("", api_type)
    builder = _PROVIDER_BUILDERS.get(fallback_key)
    if builder:
        logger.debug(
            "_build_provider: 精确匹配失败，回退到兼容 provider | key={}",
            key,
        )
        return builder(ep)

    # 3. 均未命中，抛出明确错误
    supported = sorted(_PROVIDER_BUILDERS.keys())
    raise ValueError(
        f"未知 provider/api_type 组合: {key}。已注册: {supported}"
    )


# ── 内置 builder 注册 ──────────────────────────────────────────────────────────
# 每个 builder 只做一件事：构造对应的 LLMProvider（不注入 model_info/compatibility）。
# Each builder does one thing: construct the corresponding LLMProvider.

@register_provider_builder("anthropic", API_TYPE_ANTHROPIC)
def _build_anthropic(ep: ModelEndpoint):
    """构造 AnthropicProvider（Anthropic 原生 SDK）。"""
    from anthropic import AsyncAnthropic
    from ..providers.anthropic import AnthropicProvider
    from .http import make_async_http_client

    # http_client 注入自定义超时/重试配置
    kwargs: dict[str, Any] = {"http_client": make_async_http_client()}
    if ep.api_key:
        kwargs["api_key"] = ep.api_key
    if ep.base_url:
        kwargs["base_url"] = ep.base_url

    client = AsyncAnthropic(**kwargs)
    return AnthropicProvider.from_client(client)


@register_provider_builder("openai", API_TYPE_OPENAI)
def _build_openai_chat(ep: ModelEndpoint):
    """构造 OpenAIChatProvider（OpenAI 官方端点）。"""
    from ..providers.openai_chat import OpenAIChatProvider

    return OpenAIChatProvider.from_config(base_url=ep.base_url, api_key=ep.api_key)


@register_provider_builder("deepseek", API_TYPE_OPENAI)
def _build_deepseek_chat(ep: ModelEndpoint):
    """构造 DeepSeekChatProvider（OpenAI 兼容协议）。"""
    from ..providers.deepseek import DeepSeekChatProvider

    return DeepSeekChatProvider.from_config(base_url=ep.base_url, api_key=ep.api_key)


@register_provider_builder("deepseek", API_TYPE_ANTHROPIC)
def _build_deepseek_anthropic(ep: ModelEndpoint):
    """构造 DeepSeekAnthropicProvider（Anthropic 兼容协议）。"""
    from ..providers.deepseek import DeepSeekAnthropicProvider

    return DeepSeekAnthropicProvider.from_config(base_url=ep.base_url, api_key=ep.api_key)


@register_provider_builder("kimi", API_TYPE_OPENAI)
def _build_kimi(ep: ModelEndpoint):
    """构造 KimiProvider（Moonshot AI，OpenAI 兼容）。"""
    from ..providers.kimi import KimiProvider

    return KimiProvider.from_config(base_url=ep.base_url, api_key=ep.api_key)


@register_provider_builder("mimo", API_TYPE_OPENAI)
def _build_mimo(ep: ModelEndpoint):
    """构造 MimoProvider（OpenAI 兼容）。"""
    from ..providers.mimo import MimoProvider

    return MimoProvider.from_config(base_url=ep.base_url, api_key=ep.api_key)


@register_provider_builder("qwen", API_TYPE_OPENAI)
def _build_qwen(ep: ModelEndpoint):
    """构造 QwenProvider（阿里云 DashScope，OpenAI 兼容）。"""
    from ..providers.qwen import QwenProvider

    return QwenProvider.from_config(base_url=ep.base_url, api_key=ep.api_key)


@register_provider_builder("gemini", API_TYPE_OPENAI)
def _build_gemini(ep: ModelEndpoint):
    """构造 GeminiProvider（Google Gemini，OpenAI 兼容接口）。"""
    from ..providers.gemini import GeminiProvider

    return GeminiProvider.from_config(base_url=ep.base_url, api_key=ep.api_key)


@register_provider_builder("ollama", API_TYPE_OLLAMA)
def _build_ollama(ep: ModelEndpoint):
    """构造 OllamaProvider（本地 Ollama 推理服务）。"""
    from ..providers.ollama import OllamaProvider

    return OllamaProvider.from_config(base_url=ep.base_url, api_key=ep.api_key)


@register_provider_builder("litellm", API_TYPE_LITELLM)
def _build_litellm(ep: ModelEndpoint):
    """构造 LiteLLMProvider（LiteLLM 代理层）。"""
    from ..providers.litellm import LiteLLMProvider

    return LiteLLMProvider.from_config(base_url=ep.base_url, api_key=ep.api_key)


# ── 兼容 builder（provider_id="" 作为回退路由）─────────────────────────────────
# 当 provider_id 未知时，按 api_type 选择通用兼容实现。
# Used as fallback when provider_id is unknown; routes by api_type only.

@register_provider_builder("", API_TYPE_OPENAI)
def _build_compatible_openai(ep: ModelEndpoint):
    """构造 CompatibleOpenAIProvider（任意 OpenAI 兼容端点的回退）。"""
    from ..providers.compatible import CompatibleOpenAIProvider

    return CompatibleOpenAIProvider.from_config(base_url=ep.base_url, api_key=ep.api_key)


@register_provider_builder("", API_TYPE_ANTHROPIC)
def _build_compatible_anthropic(ep: ModelEndpoint):
    """构造 CompatibleAnthropicProvider（任意 Anthropic 兼容端点的回退）。"""
    from anthropic import AsyncAnthropic
    from ..providers.compatible import CompatibleAnthropicProvider
    from .http import make_async_http_client

    kwargs: dict[str, Any] = {"http_client": make_async_http_client()}
    if ep.api_key:
        kwargs["api_key"] = ep.api_key
    if ep.base_url:
        kwargs["base_url"] = ep.base_url

    client = AsyncAnthropic(**kwargs)
    return CompatibleAnthropicProvider.from_client(client)


@register_provider_builder("", API_TYPE_RESPONSES)
def _build_compatible_responses(ep: ModelEndpoint):
    """构造 CompatibleResponsesAPIProvider（OpenAI Responses API 兼容端点的回退）。"""
    from ..providers.compatible import CompatibleResponsesAPIProvider

    return CompatibleResponsesAPIProvider.from_config(base_url=ep.base_url, api_key=ep.api_key)
