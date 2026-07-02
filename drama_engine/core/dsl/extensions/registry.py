"""Domain extension declarations.

本模块只声明扩展能力名称和说明，不实现具体游戏规则。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class DomainExtensionSpec:
    """A domain extension capability declaration."""

    name: str
    description: str
    capabilities: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Validate extension declaration."""
        assert isinstance(self.name, str) and self.name.strip(), "extension name 不能为空"
        assert isinstance(self.description, str), "extension description 必须是字符串"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable extension description."""
        return {
            "name": self.name,
            "description": self.description,
            "capabilities": list(self.capabilities),
        }


class DomainExtensionRegistry:
    """Registry for domain extensions."""

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._extensions: dict[str, DomainExtensionSpec] = {}

    def register(self, spec: DomainExtensionSpec) -> None:
        """Register a domain extension."""
        assert isinstance(spec, DomainExtensionSpec), "spec 必须是 DomainExtensionSpec"
        self._extensions[spec.name] = spec

    def has(self, name: str | None) -> bool:
        """Return whether an extension name is registered."""
        return isinstance(name, str) and name in self._extensions

    def names(self) -> list[str]:
        """Return registered extension names."""
        return sorted(self._extensions.keys())

    def describe(self, name: str) -> dict[str, Any]:
        """Return extension description."""
        assert self.has(name), f"domain extension 未注册: {name}"
        return self._extensions[name].to_dict()

    def describe_all(self) -> list[dict[str, Any]]:
        """Return all extension descriptions."""
        return [self.describe(name) for name in self.names()]


def build_default_domain_extension_registry() -> DomainExtensionRegistry:
    """Build default domain extension declarations."""
    registry = DomainExtensionRegistry()
    registry.register(DomainExtensionSpec(
        name="board",
        description="棋盘/地图状态、坐标、移动和落子等领域能力。",
        capabilities=("board_state", "move_action", "position_view"),
    ))
    registry.register(DomainExtensionSpec(
        name="cards",
        description="通用卡牌、手牌、牌堆、弃牌堆和卡牌动作能力。",
        capabilities=("deck_state", "hand_state", "card_action"),
    ))
    registry.register(DomainExtensionSpec(
        name="dice",
        description="骰子、随机检定和可回放随机事件能力。",
        capabilities=("dice_roll", "random_event"),
    ))
    registry.register(DomainExtensionSpec(
        name="economy",
        description="资源、货币、交易、建造和经济状态能力。",
        capabilities=("resource_state", "trade_action", "build_action"),
    ))
    registry.register(DomainExtensionSpec(
        name="story",
        description="剧情、地点、任务、NPC 和叙事状态能力。",
        capabilities=("world_state", "quest_state", "npc_state"),
    ))
    registry.register(DomainExtensionSpec(
        name="avalon",
        description="阿瓦隆任务、队伍、投票和刺杀阶段的规则扩展能力。",
        capabilities=("quest_rules", "team_vote", "mission_resolution", "assassination"),
    ))
    return registry
