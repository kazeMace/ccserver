"""Registry declarations for game packs.

Game pack 是具体游戏规则进入系统的边界。这里不写 UNO、五子棋、
狼人杀等规则逻辑，只保存插件 ID、说明、需要的领域扩展等元信息。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class GamePackSpec:
    """Game pack metadata."""

    plugin: str
    description: str
    required_extensions: tuple[str, ...] = field(default_factory=tuple)
    supported_runtimes: tuple[str, ...] = ("game_session",)

    def __post_init__(self) -> None:
        """Validate game pack metadata."""
        assert isinstance(self.plugin, str) and self.plugin.strip(), "game_pack.plugin 不能为空"
        assert isinstance(self.description, str), "game_pack.description 必须是字符串"

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable metadata."""
        return {
            "plugin": self.plugin,
            "description": self.description,
            "required_extensions": list(self.required_extensions),
            "supported_runtimes": list(self.supported_runtimes),
        }


class GamePackRegistry:
    """Registry for game pack metadata."""

    def __init__(self) -> None:
        """Initialize empty registry."""
        self._items: dict[str, GamePackSpec] = {}

    def register(self, spec: GamePackSpec) -> None:
        """Register a game pack."""
        assert isinstance(spec, GamePackSpec), "spec 必须是 GamePackSpec"
        self._items[spec.plugin] = spec

    def has(self, plugin: str | None) -> bool:
        """Return whether a game pack plugin is registered."""
        return isinstance(plugin, str) and plugin in self._items

    def names(self) -> list[str]:
        """Return registered game pack plugin IDs."""
        return sorted(self._items.keys())

    def describe(self, plugin: str) -> dict[str, Any]:
        """Return game pack metadata."""
        assert self.has(plugin), f"game_pack 未注册: {plugin}"
        return self._items[plugin].to_dict()

    def describe_all(self) -> list[dict[str, Any]]:
        """Return all game pack metadata."""
        return [self.describe(name) for name in self.names()]


def build_default_game_pack_registry() -> GamePackRegistry:
    """Build default game pack registry.

    默认只注册示例包，证明声明链路；真实 marketplace 包后续动态注册。
    """
    registry = GamePackRegistry()
    registry.register(GamePackSpec(
        plugin="builtin.party.free_discussion",
        description="内置自由讨论/投票派对游戏示例包。",
        required_extensions=(),
    ))
    return registry
