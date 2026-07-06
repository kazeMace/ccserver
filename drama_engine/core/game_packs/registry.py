"""GamePack 运行层注册表（机制集合声明）。

区别于 `core/dsl/game_packs/registry.py`（声明层，marketplace 元数据）：
- 本文件是「运行层」：plugin_id → GamePackManifest，manifest 说明这个包引入哪些机制、
  默认 config、需要哪些 extensions，以及把机制注册进 PluginRegistry 的注册函数。

GamePack 本身不含规则逻辑；规则逻辑在各机制里，机制注册为 DSL 的 effect/condition。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GamePackManifest:
    """一个 GamePack 的声明式定义（机制集合）。

    字段：
      plugin_id           — 引用名，例如 "builtin.board"。
      description         — 说明。
      mechanisms          — 本包提供的机制名列表（effect/condition 名），供文档/校验展示。
      default_config      — 默认配置，会与 DSL game_pack.config 合并后注入运行时。
      required_extensions — 需要在 DSL extensions 声明的领域能力，例如 ("board",)。
      register            — 把本包机制注册进 PluginRegistry 的函数：register(api) -> None。
    """

    plugin_id: str
    description: str
    register: Callable[[Any], None]
    mechanisms: tuple[str, ...] = ()
    default_config: dict[str, Any] = field(default_factory=dict)
    required_extensions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        assert isinstance(self.plugin_id, str) and self.plugin_id.strip(), "plugin_id 不能为空"
        assert callable(self.register), "register 必须可调用"

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化元信息（不含 register 函数）。"""
        return {
            "plugin_id": self.plugin_id,
            "description": self.description,
            "mechanisms": list(self.mechanisms),
            "default_config": dict(self.default_config),
            "required_extensions": list(self.required_extensions),
        }


class GamePackRuntimeRegistry:
    """运行层 GamePack 注册表：plugin_id → GamePackManifest。"""

    def __init__(self) -> None:
        """初始化空注册表。"""
        self._manifests: dict[str, GamePackManifest] = {}

    def register(self, manifest: GamePackManifest) -> None:
        """注册一个 GamePack manifest。"""
        assert isinstance(manifest, GamePackManifest), "manifest 必须是 GamePackManifest"
        self._manifests[manifest.plugin_id] = manifest
        logger.info("[GamePackRuntimeRegistry] 注册 game_pack=%s", manifest.plugin_id)

    def has(self, plugin_id: str) -> bool:
        """检查 plugin_id 是否注册。"""
        return isinstance(plugin_id, str) and plugin_id in self._manifests

    def get(self, plugin_id: str) -> GamePackManifest:
        """按 plugin_id 获取 manifest。"""
        assert self.has(plugin_id), f"game_pack 未注册: {plugin_id}"
        return self._manifests[plugin_id]

    def names(self) -> list[str]:
        """返回已注册 plugin_id 列表。"""
        return sorted(self._manifests.keys())

    def install(self, plugin_id: str, plugin_api: Any) -> dict[str, Any]:
        """把指定 GamePack 的机制注册进 PluginRegistry，返回其默认 config。

        参数：
          plugin_id  — GamePack 引用名。
          plugin_api — PluginApi（暴露 register_effect/register_condition 等）。

        返回：
          该包的默认 config 副本，供运行时与 DSL config 合并。
        """
        manifest = self.get(plugin_id)
        manifest.register(plugin_api)
        logger.info("[GamePackRuntimeRegistry] 安装 game_pack=%s 的机制", plugin_id)
        return dict(manifest.default_config)


def build_default_game_pack_runtime_registry() -> GamePackRuntimeRegistry:
    """构建默认运行层 GamePack 注册表，注册所有内置机制集合。"""
    from drama_engine.core.game_packs.builtins import register_builtin_game_packs

    registry = GamePackRuntimeRegistry()
    register_builtin_game_packs(registry)
    return registry


__all__ = [
    "GamePackManifest",
    "GamePackRuntimeRegistry",
    "build_default_game_pack_runtime_registry",
]
