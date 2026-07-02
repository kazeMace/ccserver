"""Registry declarations for game packs and rule sets.

Game pack / rule set 是具体游戏规则进入系统的边界。这里不写 UNO、五子棋、
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


@dataclass(frozen=True, slots=True)
class RuleSetSpec:
    """Rule set metadata."""

    plugin: str
    domain: str
    description: str
    required_extensions: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Validate rule set metadata."""
        assert isinstance(self.plugin, str) and self.plugin.strip(), "rule_set.plugin 不能为空"
        assert isinstance(self.domain, str) and self.domain.strip(), "rule_set.domain 不能为空"
        assert isinstance(self.description, str), "rule_set.description 必须是字符串"

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-serializable metadata."""
        return {
            "plugin": self.plugin,
            "domain": self.domain,
            "description": self.description,
            "required_extensions": list(self.required_extensions),
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


class RuleSetRegistry:
    """Registry for rule set metadata."""

    def __init__(self) -> None:
        """Initialize empty registry."""
        self._items: dict[str, RuleSetSpec] = {}

    def register(self, spec: RuleSetSpec) -> None:
        """Register a rule set."""
        assert isinstance(spec, RuleSetSpec), "spec 必须是 RuleSetSpec"
        self._items[spec.plugin] = spec

    def has(self, plugin: str | None) -> bool:
        """Return whether a rule set plugin is registered."""
        return isinstance(plugin, str) and plugin in self._items

    def names(self) -> list[str]:
        """Return registered rule set plugin IDs."""
        return sorted(self._items.keys())

    def describe(self, plugin: str) -> dict[str, Any]:
        """Return rule set metadata."""
        assert self.has(plugin), f"rule_set 未注册: {plugin}"
        return self._items[plugin].to_dict()

    def describe_all(self) -> list[dict[str, Any]]:
        """Return all rule set metadata."""
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


def build_default_rule_set_registry() -> RuleSetRegistry:
    """Build default rule set registry.

    默认 rule_set 只表达领域边界，不包含具体游戏规则原语。
    """
    registry = RuleSetRegistry()
    registry.register(RuleSetSpec(
        plugin="builtin.board.generic",
        domain="board",
        description="通用棋盘动作规则接口，占位实现。",
        required_extensions=("board",),
    ))
    registry.register(RuleSetSpec(
        plugin="builtin.board.gomoku_lite",
        domain="board",
        description="五子棋 Lite 落子规则接口。",
        required_extensions=("board",),
    ))
    registry.register(RuleSetSpec(
        plugin="builtin.board.xiangqi_lite",
        domain="board",
        description="象棋 Lite 走子规则接口。",
        required_extensions=("board",),
    ))
    registry.register(RuleSetSpec(
        plugin="builtin.board.go_lite",
        domain="board",
        description="围棋简化版 Lite 落子与数地规则接口。",
        required_extensions=("board",),
    ))
    registry.register(RuleSetSpec(
        plugin="builtin.board.checkers_lite",
        domain="board",
        description="跳棋 Lite 移动与跳吃规则接口。",
        required_extensions=("board",),
    ))
    registry.register(RuleSetSpec(
        plugin="builtin.board.flight_chess_lite",
        domain="board",
        description="飞行棋 Lite 掷骰移动规则接口。",
        required_extensions=("board", "dice"),
    ))
    registry.register(RuleSetSpec(
        plugin="builtin.board.monopoly_lite",
        domain="board",
        description="大富翁 Lite 地图移动规则接口。",
        required_extensions=("board", "dice", "economy"),
    ))
    registry.register(RuleSetSpec(
        plugin="builtin.cards.generic",
        domain="cards",
        description="通用卡牌动作规则接口，占位实现。",
        required_extensions=("cards",),
    ))
    registry.register(RuleSetSpec(
        plugin="builtin.cards.uno_lite",
        domain="cards",
        description="UNO Lite 出牌/摸牌规则接口。",
        required_extensions=("cards",),
    ))
    registry.register(RuleSetSpec(
        plugin="builtin.cards.exploding_kittens_lite",
        domain="cards",
        description="炸弹猫 Lite 功能牌/摸牌/拆除规则接口。",
        required_extensions=("cards",),
    ))
    registry.register(RuleSetSpec(
        plugin="builtin.cards.texas_holdem_party_lite",
        domain="cards",
        description="德州扑克简化派对版下注与摊牌规则接口。",
        required_extensions=("cards", "economy"),
    ))
    registry.register(RuleSetSpec(
        plugin="builtin.cards.card_event_party_lite",
        domain="cards",
        description="牌堆事件派对抽牌与评分规则接口。",
        required_extensions=("cards",),
    ))
    registry.register(RuleSetSpec(
        plugin="builtin.story.generic",
        domain="story",
        description="通用固定流程剧情规则接口，占位实现。",
        required_extensions=("story",),
    ))
    registry.register(RuleSetSpec(
        plugin="builtin.story.dice_map_adventure_lite",
        domain="story",
        description="骰子地图冒险 Lite 探索与事件规则接口。",
        required_extensions=("board", "dice", "story"),
    ))
    registry.register(RuleSetSpec(
        plugin="builtin.story.dnd_fixed_adventure",
        domain="story",
        description="DND Lite 固定冒险检定与圣徽结算规则接口。",
        required_extensions=("story", "dice"),
    ))
    registry.register(RuleSetSpec(
        plugin="builtin.story.coc_fixed_mystery",
        domain="story",
        description="COC Lite 固定调查线索与理智规则接口。",
        required_extensions=("story", "dice"),
    ))
    registry.register(RuleSetSpec(
        plugin="builtin.story.campaign_lite",
        domain="story",
        description="剧情跑团 Lite 章节推进规则接口。",
        required_extensions=("story", "dice"),
    ))
    registry.register(RuleSetSpec(
        plugin="builtin.story.text_adventure_lite",
        domain="story",
        description="文字冒险 Lite 观察与行动规则接口。",
        required_extensions=("story",),
    ))
    registry.register(RuleSetSpec(
        plugin="builtin.story.agent_dm_adventure_lite",
        domain="story",
        description="Agent DM 冒险 Lite 裁定规则接口。",
        required_extensions=("story", "dice"),
    ))
    registry.register(RuleSetSpec(
        plugin="builtin.economy.asset_trading_lite",
        domain="economy",
        description="资产交易派对 Lite 报价与成交规则接口。",
        required_extensions=("economy",),
    ))
    return registry
