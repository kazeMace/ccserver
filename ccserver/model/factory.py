"""
factory — 运行时 ModelAdapter 选择工厂。

提供两套 API：
  1. AdapterFactory.build(endpoint) — 新 API，基于 ModelEndpoint 构造 adapter，
     自动注入 model_info 和 compat，推荐所有新代码使用。
  2. get_adapter(provider, **config)  — 旧 API，保留向后兼容，内部调用 ProviderRegistry。

迁移指引：
  旧：adapter = get_adapter("anthropic")
  新：adapter = AdapterFactory.build(ModelEndpoint.from_env())
"""

from __future__ import annotations

import os
import httpx
from typing import Any, Callable

from loguru import logger

from .adapter import ModelAdapter
from .endpoint import ModelEndpoint, API_TYPE_ANTHROPIC, API_TYPE_OPENAI, API_TYPE_ZHIPUAI, API_TYPE_VOLCANO
from .plugins.registry import get_provider_registry


# ── api_type → builder 注册表（OCP）────────────────────────────────────────────────
#
# 设计意图：满足开闭原则。新增一种 api_type（新模型供应商协议）时，
# 只需新增一个 builder 函数并用 @register_api_builder("xxx") 装饰，
# 无需修改 _build_adapter_for_api_type 的分派逻辑。
#
# Builder 签名：(ModelEndpoint) -> ModelAdapter
#   入参：已 resolve() 的 ModelEndpoint（api_type 一定非 None）
#   返回：未注入 model_info/compat 的 ModelAdapter 实例
ApiTypeBuilder = Callable[[ModelEndpoint], ModelAdapter]

# 全局注册表：api_type 字符串 → builder 函数
_API_TYPE_BUILDERS: dict[str, ApiTypeBuilder] = {}


def register_api_builder(api_type: str) -> Callable[[ApiTypeBuilder], ApiTypeBuilder]:
    """
    装饰器：将一个 builder 函数注册到 _API_TYPE_BUILDERS。

    Args:
        api_type: 该 builder 负责的 api_type 字符串，如 API_TYPE_ANTHROPIC。

    Returns:
        装饰器本身。被装饰函数原样返回（仅产生注册副作用）。

    Raises:
        AssertionError: api_type 为空，或已被其他 builder 占用（防止重复注册）。

    Usage:
        @register_api_builder(API_TYPE_ANTHROPIC)
        def _build_anthropic(ep: ModelEndpoint) -> ModelAdapter:
            ...
    """
    assert api_type, "api_type must not be empty"
    assert api_type not in _API_TYPE_BUILDERS, (
        f"api_type {api_type!r} already registered by "
        f"{_API_TYPE_BUILDERS[api_type].__name__}"
    )

    def _decorator(builder: ApiTypeBuilder) -> ApiTypeBuilder:
        _API_TYPE_BUILDERS[api_type] = builder
        logger.debug("register_api_builder | api_type={} builder={}", api_type, builder.__name__)
        return builder

    return _decorator


class AdapterFactory:
    """
    根据 ModelEndpoint 构造 ModelAdapter，并注入 model_info 和 compat。

    这是取代 get_adapter(provider, **config) 的新入口。
    根据 endpoint.api_type 选择对应的 Adapter 实现，
    然后从 ModelInfoRegistry 和 CompatRegistry 注入能力信息。

    Usage:
        endpoint = ModelEndpoint.from_env()
        adapter = AdapterFactory.build(endpoint)
        # adapter.model_info  → ModelInfo 或 None
        # adapter.compat      → ModelCompat（协议兼容性）
        # adapter.supports_image → bool（模型是否理解图像）
    """

    @staticmethod
    def build(endpoint: ModelEndpoint) -> ModelAdapter:
        """
        根据 ModelEndpoint 构造对应的 ModelAdapter。

        步骤：
          1. endpoint.resolve() 补全所有 None 字段
          2. 根据 api_type 选择 adapter 实现
          3. 从 ModelInfoRegistry 查询 model_info
          4. 从 CompatRegistry 查询 compat
          5. 注入到 adapter 实例

        Args:
            endpoint: ModelEndpoint 实例（允许字段不完整，内部 resolve()）

        Returns:
            注入了 model_info 和 compat 的 ModelAdapter 实例

        Raises:
            ValueError: api_type 未知或对应 SDK 未安装
        """
        assert endpoint is not None, "endpoint must not be None"
        assert endpoint.model_id, "endpoint.model_id must not be empty"

        # Step 1: 补全所有 None 字段
        ep = endpoint.resolve()

        logger.debug("AdapterFactory.build | model_id={} api_type={} provider={} base_url={}",
                     ep.model_id, ep.api_type, ep.provider, ep.base_url)

        # Step 2: 根据 api_type 创建对应的 adapter 实例
        adapter = _build_adapter_for_api_type(ep)

        # Step 3: 注入 model_info（从 ModelInfoRegistry 查）
        from .info.registry import get_registry
        adapter.model_info = get_registry().get(ep.model_id)

        # Step 4: 注入 compat（从 CompatRegistry 查，带优先级匹配）
        from .info.compat_registry import get_compat_registry
        adapter.compat = get_compat_registry().get(ep.model_id, ep.api_type)

        logger.info(
            "AdapterFactory.build 完成 | model_id={} api_type={} "
            "supports_image={} supports_image_in_tool_result={} supports_tools={}",
            ep.model_id, ep.api_type,
            adapter.supports_image,
            adapter.supports_image_in_tool_result,
            adapter.supports_tools,
        )

        return adapter


def _build_adapter_for_api_type(ep: ModelEndpoint) -> ModelAdapter:
    """
    根据 api_type 创建底层 adapter 实例（不含 model_info/compat 注入）。

    通过 _API_TYPE_BUILDERS 注册表查表分派，满足开闭原则：
    新增 api_type 只需注册新 builder，本函数无需改动。

    Args:
        ep: 已 resolve() 的 ModelEndpoint（api_type 一定非 None）

    Returns:
        未注入能力信息的 ModelAdapter 实例

    Raises:
        ValueError: api_type 未在注册表中找到对应 builder
    """
    api_type = ep.api_type or API_TYPE_OPENAI

    builder = _API_TYPE_BUILDERS.get(api_type)
    if builder is None:
        supported = ", ".join(sorted(_API_TYPE_BUILDERS.keys()))
        raise ValueError(
            f"Unknown api_type: {api_type!r}. Supported: {supported}"
        )

    return builder(ep)


@register_api_builder(API_TYPE_ANTHROPIC)
def _build_anthropic(ep: ModelEndpoint) -> ModelAdapter:
    """构造 AnthropicAdapter，使用 ep.base_url / ep.api_key。"""
    from anthropic import AsyncAnthropic
    from .anthropic_adapter import AnthropicAdapter

    kwargs: dict[str, Any] = {
        "http_client": httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=600.0),
            limits=httpx.Limits(keepalive_expiry=5),
        ),
    }
    if ep.api_key:
        kwargs["api_key"] = ep.api_key
    if ep.base_url:
        kwargs["base_url"] = ep.base_url

    client = AsyncAnthropic(**kwargs)
    logger.debug("_build_anthropic | base_url={}", ep.base_url)
    return AnthropicAdapter(client)


@register_api_builder(API_TYPE_OPENAI)
def _build_openai(ep: ModelEndpoint) -> ModelAdapter:
    """构造 OpenAIAdapter，使用 ep.base_url / ep.api_key。"""
    from .openai_adapter import OpenAIAdapter

    adapter = OpenAIAdapter.from_config(base_url=ep.base_url, api_key=ep.api_key)
    logger.debug("_build_openai | base_url={}", ep.base_url)
    return adapter


@register_api_builder(API_TYPE_ZHIPUAI)
def _build_zhipuai(ep: ModelEndpoint) -> ModelAdapter:
    """构造 ZhipuAIAdapter，使用 ep.api_key。"""
    from .zhipuai_adapter import ZhipuAIAdapter

    api_key = ep.api_key or os.getenv("ZHIPUAI_API_KEY", "")
    assert api_key, "ZhipuAI api_key is required. Set ZHIPUAI_API_KEY env var."

    logger.debug("_build_zhipuai | api_key_set={}", bool(api_key))
    return ZhipuAIAdapter(api_key=api_key, base_url=ep.base_url)


@register_api_builder(API_TYPE_VOLCANO)
def _build_volcano(ep: ModelEndpoint) -> ModelAdapter:
    """构造 VolcanoAdapter，使用 ep.api_key。"""
    try:
        from volcenginesdkarkruntime import Ark
    except ImportError:
        raise ImportError(
            "volcenginesdkarkruntime package is required for Volcano api_type. "
            "Install it with: pip install volcengine-python-sdk[ark]"
        )
    from .volcano_adapter import VolcanoAdapter

    api_key = ep.api_key or os.getenv("VOLC_ACCESSKEY", "")
    base_url = ep.base_url or os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    assert api_key, "Volcano api_key is required. Set VOLC_ACCESSKEY env var."

    client = Ark(
        api_key=api_key,
        base_url=base_url,
        http_client=httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=600.0),
            limits=httpx.Limits(keepalive_expiry=5),
        ),
    )
    logger.debug("_build_volcano | base_url={}", base_url)
    return VolcanoAdapter(client)


# ── 旧 API（向后兼容）────────────────────────────────────────────────────────────────


def get_adapter(provider: str | None = None, **config: Any) -> ModelAdapter:
    """
    根据 provider 名称返回对应的 ModelAdapter 实例。

    【兼容旧接口，建议新代码使用 AdapterFactory.build(endpoint)】

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
