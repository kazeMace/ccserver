"""内置 GamePack（机制集合）声明。

每个内置 GamePack 是一组同领域机制的声明式集合。DSL 通过 game_pack.plugin 引用它，
运行时把其机制注册进 PluginRegistry。GamePack 本身不含规则逻辑。
"""

from __future__ import annotations

from drama_engine.core.game_packs.mechanisms import (
    affinity,
    board,
    cards,
    cinematic,
    dice,
    economy,
    inventory,
    narrative,
    social,
    stats,
)
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
        description="社交推理机制集合：计票、出局、夜晚结算、阵营清空判定、击杀/记录/发言顺序等。适用狼人杀/阿瓦隆/谁是卧底。",
        register=social.register,
        mechanisms=(
            "tally_votes", "eliminate", "resolve_night", "social.faction_cleared",
            "social.kill", "social.record_target", "social.record_current_deaths",
            "social.build_speech_order", "social.just_died", "social.is_first_round",
        ),
        default_config={},
        required_extensions=(),
        projection_profile=social.build_social_projection_profile(),
    ))
    registry.register(GamePackManifest(
        plugin_id="builtin.inventory",
        description="背包/道具机制集合：获得、消耗、转移物品，拥有判定。支持计数型与富属性型，运行中可扩展。",
        register=inventory.register,
        mechanisms=("grant_item", "use_item", "transfer_item", "inventory.has_item"),
        default_config={},
        required_extensions=(),
    ))
    registry.register(GamePackManifest(
        plugin_id="builtin.stats",
        description="角色面板机制集合：属性增量修改、阈值判定。适用血量/金币/等级/好感度等可变属性。",
        register=stats.register,
        mechanisms=("adjust_attr", "stats.attr_at_least", "stats.attr_below"),
        default_config={},
        required_extensions=(),
    ))
    registry.register(GamePackManifest(
        plugin_id="builtin.cinematic",
        description="播片式剧情机制：逐条播放预制对话，点击推进，关键节点选择。适用 Galgame/视觉小说/互动影视。",
        register=cinematic.register,
        mechanisms=("cinematic_emit_line",),
        default_config={"mode": "visual_novel", "auto_advance_on_video": True},
        required_extensions=(),
        projection_profile=cinematic.build_cinematic_projection_profile(),
    ))
    registry.register(GamePackManifest(
        plugin_id="builtin.narrative",
        description="叙事机制集合：记录分支选择、搜集线索、按进度选定结局。适用文字冒险/分支剧情/剧本杀叙事。",
        register=narrative.register,
        mechanisms=(
            "record_choice", "collect_clue", "set_ending",
            "narrative.clue_collected", "narrative.reached_ending",
        ),
        default_config={},
        required_extensions=(),
        projection_profile=narrative.build_narrative_projection_profile(),
    ))
    registry.register(GamePackManifest(
        plugin_id="builtin.affinity",
        description="好感/关系机制集合：设置好感、按好感配对、淘汰最低分、互选判定。适用综艺 AI/恋综/约会。",
        register=affinity.register,
        mechanisms=(
            "set_affinity", "pair_by_affinity", "eliminate_lowest",
            "affinity.mutual_at_least",
        ),
        default_config={},
        required_extensions=(),
        projection_profile=affinity.build_affinity_projection_profile(),
    ))


__all__ = ["register_builtin_game_packs"]
