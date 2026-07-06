"""内置 GamePack（机制集合）声明。

每个内置 GamePack 是一组同领域机制的声明式集合。DSL 通过 game_pack.plugin 引用它，
运行时把其机制注册进 PluginRegistry。GamePack 本身不含规则逻辑。
"""

from __future__ import annotations

from drama_engine.core.game_packs.mechanisms import board, cards, dice, economy, social
from drama_engine.core.game_packs.registry import GamePackManifest, GamePackRuntimeRegistry


def register_builtin_game_packs(registry: GamePackRuntimeRegistry) -> None:
    """把内置机制集合注册进运行层 GamePack 注册表。"""
    registry.register(GamePackManifest(
        plugin_id="builtin.board",
        description="棋盘机制集合：落子、连线判定、空位判定。适用五子棋/象棋/围棋/跳棋。",
        register=board.register,
        mechanisms=("board_place", "board.connect_n", "board.cell_empty"),
        default_config={"board_size": 15},
        required_extensions=("board",),
    ))
    registry.register(GamePackManifest(
        plugin_id="builtin.dice",
        description="骰子机制集合：可回放掷骰、环形轨道移动。适用大富翁/飞行棋。",
        register=dice.register,
        mechanisms=("roll_dice", "advance_on_track"),
        default_config={},
        required_extensions=("dice",),
    ))
    registry.register(GamePackManifest(
        plugin_id="builtin.economy",
        description="经济机制集合：加钱、扣钱、转账、破产判定。适用大富翁/资产交易。",
        register=economy.register,
        mechanisms=("credit", "debit", "transfer", "economy.bankrupt"),
        default_config={},
        required_extensions=("economy",),
    ))
    registry.register(GamePackManifest(
        plugin_id="builtin.cards",
        description="卡牌机制集合：摸牌、出牌、手牌清空判定。适用 UNO/爆炸猫/德州扑克。",
        register=cards.register,
        mechanisms=("draw_card", "play_card", "cards.hand_empty"),
        default_config={},
        required_extensions=("cards",),
    ))
    registry.register(GamePackManifest(
        plugin_id="builtin.social",
        description="社交推理机制集合：计票、出局、阵营清空判定。适用狼人杀/阿瓦隆/谁是卧底。",
        register=social.register,
        mechanisms=("tally_votes", "eliminate", "social.faction_cleared"),
        default_config={},
        required_extensions=(),
    ))


__all__ = ["register_builtin_game_packs"]
