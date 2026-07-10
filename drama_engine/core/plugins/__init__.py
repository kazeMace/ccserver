"""插件注册表包 — 运行时 dispatch hub。

提供 PluginRegistry（能力注册表）和 PluginApi（插件窄接口），
以及内置插件类和 context 数据类。
"""

from drama_engine.core.plugins.registry import (
    CoreViewsPlugin,
    EffectContext,
    PluginApi,
    PluginRegistry,
    ViewContext,
    ViewEvent,
    build_default_plugin_registry,
)

__all__ = [
    "CoreViewsPlugin",
    "EffectContext",
    "PluginApi",
    "PluginRegistry",
    "ViewContext",
    "ViewEvent",
    "build_default_plugin_registry",
]
