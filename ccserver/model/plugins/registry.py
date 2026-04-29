"""
registry — ProviderRegistry 全局注册表。

进程级单例。所有 ProviderPlugin 通过 import 注册后，
系统可以通过 provider id 动态创建对应的 ModelAdapter。
"""

from __future__ import annotations

from loguru import logger

from .base import ProviderPlugin
from ccserver.model.adapter import ModelAdapter
from ccserver.model.info.registry import ModelInfoRegistry, get_registry


class ProviderRegistry:
    """
    全局 ProviderPlugin 注册表。

    内部用 dict[str, ProviderPlugin] 存储，key 为 plugin.id。
    """

    def __init__(self):
        self._plugins: dict[str, ProviderPlugin] = {}
        self._initialized: bool = False

    # ── 注册 ──────────────────────────────────────────────────────────────────

    def register(self, plugin: ProviderPlugin) -> None:
        """
        注册一个 ProviderPlugin。

        Args:
            plugin: ProviderPlugin 实例

        Raises:
            AssertionError: plugin 不符合 ProviderPlugin 协议
        """
        assert plugin is not None, "plugin must not be None"
        assert isinstance(plugin.id, str) and plugin.id, \
            f"plugin.id must be a non-empty string, got: {plugin.id!r}"

        provider_id = plugin.id.lower()

        if provider_id in self._plugins:
            logger.warning("ProviderRegistry 覆盖注册 | id={} old={} new={}",
                           provider_id, self._plugins[provider_id].name, plugin.name)

        self._plugins[provider_id] = plugin

        # 将插件支持的模型注册到 ModelInfoRegistry
        model_registry = get_registry()
        plugin.register_models(model_registry)

        logger.info("ProviderRegistry 注册插件 | id={} name={} transport={}",
                    provider_id, plugin.name, plugin.transport_type)

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def get(self, provider_id: str) -> ProviderPlugin | None:
        """
        根据 provider id 查找插件。

        Args:
            provider_id: provider id，大小写不敏感

        Returns:
            ProviderPlugin 或 None
        """
        assert isinstance(provider_id, str), f"provider_id must be str, got {type(provider_id)}"
        return self._plugins.get(provider_id.lower())

    def create_adapter(
        self,
        provider_id: str,
        api_key: str | None = None,
        base_url: str | None = None,
        **config,
    ) -> ModelAdapter:
        """
        根据 provider id 创建 ModelAdapter。

        这是 get_adapter() 函数的底层实现。

        Args:
            provider_id: provider id
            api_key:     API 密钥（None 时由 plugin 自己从环境变量获取）
            base_url:    API 端点（None 时使用默认值）
            **config:    额外配置参数

        Returns:
            ModelAdapter 实例

        Raises:
            ValueError: 未知的 provider id
        """
        provider_id = provider_id.lower()
        plugin = self._plugins.get(provider_id)
        if plugin is None:
            supported = sorted(self._plugins.keys())
            raise ValueError(
                f"Unknown provider: {provider_id!r}. Supported: {supported}"
            )

        adapter = plugin.create_adapter(api_key=api_key, base_url=base_url, **config)
        logger.debug("ProviderRegistry 创建 adapter | provider={} adapter={}",
                     provider_id, type(adapter).__name__)
        return adapter

    def list_providers(self) -> list[str]:
        """列出所有已注册的 provider id。"""
        return sorted(self._plugins.keys())

    def get_plugin_info(self) -> list[dict]:
        """
        返回所有已注册插件的摘要信息。

        Returns:
            [{"id": str, "name": str, "transport": str}, ...]
        """
        return [
            {
                "id": p.id,
                "name": p.name,
                "transport": p.transport_type,
            }
            for p in self._plugins.values()
        ]

    # ── 初始化 ────────────────────────────────────────────────────────────────

    def _init_defaults(self) -> None:
        """
        注册所有内置 ProviderPlugin。

        每个插件在创建时会：
        1. 调用 register_models() 将模型注册到 ModelInfoRegistry
        2. 如果有 create_media_provider()，注册到 MediaUnderstandingRegistry
        """
        if self._initialized:
            return
        self._initialized = True

        # 这些 import 会触发类的导入（在 __init__.py 中统一管理）
        from .anthropic import AnthropicPlugin
        from .openai import OpenAIPlugin
        from .volcano import VolcanoPlugin
        from .qwen import QwenPlugin
        from .zhipuai import ZhipuAIPlugin

        # 注册各插件
        self.register(AnthropicPlugin())

        # OpenAI 兼容系列 —— 一个类创建多个实例，通过 id/name/base_url 区分
        self.register(OpenAIPlugin(
            provider_id="openai",
            name="OpenAI",
            default_base_url="https://api.openai.com/v1",
            env_api_key="OPENAI_API_KEY",
        ))
        self.register(OpenAIPlugin(
            provider_id="openrouter",
            name="OpenRouter",
            default_base_url="https://openrouter.ai/api/v1",
            env_api_key="OPENROUTER_API_KEY",
        ))
        self.register(OpenAIPlugin(
            provider_id="ollama",
            name="Ollama",
            default_base_url="http://localhost:11434/v1",
            env_api_key="",  # Ollama 不需要 API Key
        ))
        self.register(OpenAIPlugin(
            provider_id="lmstudio",
            name="LM Studio",
            default_base_url="http://localhost:1234/v1",
            env_api_key="",  # LM Studio 不需要 API Key
        ))
        # OneAPI 和 Generic 需要从环境变量读取 base_url
        import os
        self.register(OpenAIPlugin(
            provider_id="oneapi",
            name="One API",
            default_base_url=os.getenv("ONEAPI_BASE_URL", ""),
            env_api_key="ONEAPI_API_KEY",
        ))
        self.register(OpenAIPlugin(
            provider_id="generic",
            name="Generic OpenAI Compatible",
            default_base_url="",  # 由调用方传入
            env_api_key="",       # 由调用方传入
        ))

        self.register(VolcanoPlugin())
        self.register(QwenPlugin())
        self.register(ZhipuAIPlugin())

        # 注册 MediaUnderstandingProvider（为有 VL 能力的 provider）
        self._register_media_providers()

        logger.info("ProviderRegistry 初始化完成 | provider 总数={}", len(self._plugins))

    def _register_media_providers(self) -> None:
        """
        为支持多模态的 plugin 注册 MediaUnderstandingProvider。

        检查每个 plugin 是否有 create_media_provider() 方法，
        如果有则调用并注册到 MediaUnderstandingRegistry。
        注册失败（如缺少 API key）只记录日志，不影响主流程。
        """
        from ccserver.model.media.registry import get_media_registry

        media_registry = get_media_registry()

        for plugin_id, plugin in self._plugins.items():
            # 检查 plugin 是否有 create_media_provider 方法
            create_fn = getattr(plugin, "create_media_provider", None)
            if create_fn is None:
                continue

            try:
                media_provider = create_fn()
                if media_provider is not None:
                    media_registry.register(media_provider)
                    logger.info(
                        "ProviderRegistry 注册媒体理解能力 | provider={} priority={}",
                        plugin_id, media_provider.auto_priority,
                    )
            except Exception as e:
                # API key 缺失或其他配置问题，不影响系统启动
                logger.debug(
                    "ProviderRegistry 跳过媒体理解注册 | provider={} reason={}",
                    plugin_id, e,
                )


# ── 进程级单例 ────────────────────────────────────────────────────────────────────

_registry_instance: ProviderRegistry | None = None


def get_provider_registry() -> ProviderRegistry:
    """
    返回进程级单例 ProviderRegistry。

    首次调用时自动注册所有内置 Plugin。

    Returns:
        ProviderRegistry 单例实例
    """
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = ProviderRegistry()
        _registry_instance._init_defaults()
    return _registry_instance
