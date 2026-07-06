"""机制库单元测试。

直接以 EffectContext / condition context 驱动各机制，验证其状态读写正确，
不依赖完整 runtime。
"""

from __future__ import annotations

from drama_engine.core.engine import SetAttr, State, StateWriter, Vocabulary
from drama_engine.core.dsl.components import EffectExecutor  # noqa: F401 - 先加载组件，避免循环导入
from drama_engine.core.dsl.plugins import EffectContext, PluginApi, PluginRegistry
from drama_engine.core.game_packs import build_default_game_pack_runtime_registry
from drama_engine.core.game_packs.mechanisms import (
    board,
    cards,
    dice,
    economy,
    inventory,
    social,
    stats,
)


def _new_state(players: int = 4) -> State:
    """构造一个带 GAME 与若干 Player 实体的空状态。"""
    names = [f"Player_{i}" for i in range(1, players + 1)]
    state = State(Vocabulary(frozenset(), frozenset(), frozenset(), frozenset()))
    state.register_entity("GAME", {"players": names})
    for name in names:
        state.register_entity(name, {"alive": True})
    return state


def _ctx(state: State, actor: str | None = None, responses=None) -> EffectContext:
    """构造 effect 执行上下文。"""
    return EffectContext(
        state=state,
        writer=StateWriter(state),
        actor=actor,
        responses=responses or [],
        scene_name="test",
        extra={},
    )


def _registry_with(*modules) -> PluginRegistry:
    """构造注册了指定机制模块的 PluginRegistry。"""
    registry = PluginRegistry()
    api = PluginApi(registry)
    for module in modules:
        module.register(api)
    return registry


def test_board_place_and_connect_n() -> None:
    """board_place 落子 + board.connect_n 五连判定。"""
    registry = _registry_with(board)
    state = _new_state()
    StateWriter(state).apply(SetAttr("Player_1", "role", "black"))
    # 横向连下 5 子
    for col in range(5):
        registry.execute_effect(
            {"type": "board_place", "position": [0, col], "piece": "black"},
            _ctx(state, actor="Player_1"),
        )
    cond_ctx = {"state": state}
    assert registry.evaluate_condition("board.connect_n", {"input": {"n": 5}}, cond_ctx) is True
    assert registry.evaluate_condition("board.connect_n", {"input": {"n": 6}}, cond_ctx) is False


def test_board_cell_empty() -> None:
    """board.cell_empty 判定空位。"""
    registry = _registry_with(board)
    state = _new_state()
    registry.execute_effect({"type": "board_place", "position": [1, 1], "piece": "x"}, _ctx(state))
    assert registry.evaluate_condition("board.cell_empty", {"position": [1, 1]}, {"state": state}) is False
    assert registry.evaluate_condition("board.cell_empty", {"position": [2, 2]}, {"state": state}) is True


def test_dice_roll_is_replayable() -> None:
    """相同种子的掷骰结果可复现。"""
    registry = _registry_with(dice)
    state_a = _new_state()
    StateWriter(state_a).apply(SetAttr("GAME", "dice_seed", 42))
    registry.execute_effect({"type": "roll_dice", "sides": 6}, _ctx(state_a))
    first = state_a.get_attr("GAME", "last_roll")

    state_b = _new_state()
    StateWriter(state_b).apply(SetAttr("GAME", "dice_seed", 42))
    registry.execute_effect({"type": "roll_dice", "sides": 6}, _ctx(state_b))
    assert state_b.get_attr("GAME", "last_roll") == first
    assert 1 <= first <= 6


def test_dice_custom_faces() -> None:
    """自定义面值 faces 应从值集中取值，而非 1..sides。"""
    registry = _registry_with(dice)
    state = _new_state()
    StateWriter(state).apply(SetAttr("GAME", "dice_seed", 5))
    registry.execute_effect({"type": "roll_dice", "faces": [0, 0, 1, 1, 2, 5]}, _ctx(state))
    assert state.get_attr("GAME", "last_rolls")[0] in {0, 1, 2, 5}


def test_dice_weighted_probability_biases_result() -> None:
    """加权概率应明显偏向高权重面。"""
    registry = _registry_with(dice)
    state = _new_state()
    StateWriter(state).apply(SetAttr("GAME", "dice_seed", 1))
    miss = 0
    for _ in range(200):
        registry.execute_effect(
            {"type": "roll_dice", "faces": ["hit", "miss"], "weights": [0.1, 0.9]},
            _ctx(state),
        )
        if state.get_attr("GAME", "last_rolls") == ["miss"]:
            miss += 1
    # 90% 权重的 miss 应占多数（远超一半）。
    assert miss > 140


def test_dice_named_defs_and_multi_roll() -> None:
    """GAME.dice_defs 里的具名骰子可被 die/dice 引用，dice 支持一次多投。"""
    registry = _registry_with(dice)
    state = _new_state()
    StateWriter(state).apply(SetAttr("GAME", "dice_seed", 3))
    StateWriter(state).apply(SetAttr("GAME", "dice_defs", {
        "d20": {"sides": 20},
        "atk": {"faces": ["hit", "miss"], "weights": [0.5, 0.5]},
    }))
    registry.execute_effect({"type": "roll_dice", "dice": ["d20", "d20"]}, _ctx(state))
    rolls = state.get_attr("GAME", "last_rolls")
    assert len(rolls) == 2 and all(1 <= r <= 20 for r in rolls)
    assert state.get_attr("GAME", "last_roll") == sum(rolls)

    registry.execute_effect({"type": "roll_dice", "die": "atk"}, _ctx(state))
    assert state.get_attr("GAME", "last_rolls")[0] in {"hit", "miss"}


def test_dice_unknown_die_id_raises() -> None:
    """引用未定义的骰子 id 应报错，不静默。"""
    import pytest
    registry = _registry_with(dice)
    state = _new_state()
    with pytest.raises(AssertionError):
        registry.execute_effect({"type": "roll_dice", "die": "ghost"}, _ctx(state))


def test_dice_advance_on_track_wraps_and_flags_start() -> None:
    """advance_on_track 环形移动并标记经过起点。"""
    registry = _registry_with(dice)
    state = _new_state()
    StateWriter(state).apply(SetAttr("GAME", "board_size", 10))
    StateWriter(state).apply(SetAttr("Player_1", "position", 8))
    StateWriter(state).apply(SetAttr("GAME", "last_roll", 5))
    registry.execute_effect({"type": "advance_on_track"}, _ctx(state, actor="Player_1"))
    assert state.get_attr("Player_1", "position") == 3  # (8+5)%10
    assert state.get_attr("GAME", "passed_start") is True


def test_economy_transfer_and_bankruptcy() -> None:
    """transfer 转账 + 破产判定。"""
    registry = _registry_with(economy)
    state = _new_state()
    StateWriter(state).apply(SetAttr("Player_1", "cash", 100))
    StateWriter(state).apply(SetAttr("Player_2", "cash", 0))
    registry.execute_effect(
        {"type": "transfer", "payer": "Player_1", "payee": "Player_2", "amount": 30},
        _ctx(state),
    )
    assert state.get_attr("Player_1", "cash") == 70
    assert state.get_attr("Player_2", "cash") == 30

    # 扣到负数触发破产
    registry.execute_effect({"type": "debit", "target": "Player_2", "amount": 100}, _ctx(state))
    assert registry.evaluate_condition("economy.bankrupt", {"entity": "Player_2"}, {"state": state}) is True


def test_social_tally_votes_and_eliminate() -> None:
    """tally_votes 计票 + eliminate 出局。"""
    registry = _registry_with(social)
    state = _new_state()
    StateWriter(state).apply(SetAttr("Player_1", "alive", True))
    responses = [
        {"actor": "Player_2", "data": {"vote": "Player_1"}},
        {"actor": "Player_3", "data": {"vote": "Player_1"}},
        {"actor": "Player_4", "data": {"vote": "Player_2"}},
    ]
    registry.execute_effect({"type": "tally_votes"}, _ctx(state, responses=responses))
    assert state.get_attr("GAME", "last_vote_target") == "Player_1"
    registry.execute_effect({"type": "eliminate"}, _ctx(state))
    assert state.get_attr("Player_1", "alive") is False


def test_social_resolve_night_respects_guard_and_save() -> None:
    """resolve_night：被守护或用解药则免死，否则出局。"""
    registry = _registry_with(social)
    # 情形1：守护免死
    state = _new_state(2)
    StateWriter(state).apply(SetAttr("GAME", "night_target", "Player_1"))
    StateWriter(state).apply(SetAttr("GAME", "guard_target", "Player_1"))
    registry.execute_effect({"type": "resolve_night"}, _ctx(state))
    assert state.get_attr("Player_1", "alive") is True
    assert state.get_attr("GAME", "night_deaths") == []

    # 情形2：无守护、无解药则出局
    StateWriter(state).apply(SetAttr("GAME", "night_target", "Player_2"))
    registry.execute_effect({"type": "resolve_night"}, _ctx(state))
    assert state.get_attr("Player_2", "alive") is False
    assert state.get_attr("GAME", "night_deaths") == ["Player_2"]


def test_social_eliminate_resolves_ref_target() -> None:
    """eliminate 支持 {ref: GAME.night_target} 解析出局对象。"""
    registry = _registry_with(social)
    state = _new_state(2)
    StateWriter(state).apply(SetAttr("GAME", "night_target", "Player_1"))
    registry.execute_effect({"type": "eliminate", "target": {"ref": "GAME.night_target"}}, _ctx(state))
    assert state.get_attr("Player_1", "alive") is False


def test_cards_draw_play_and_hand_empty() -> None:
    """draw_card 摸牌 + play_card 出牌 + hand_empty 判定。"""
    registry = _registry_with(cards)
    state = _new_state()
    StateWriter(state).apply(SetAttr("GAME", "deck", ["R1", "R2"]))
    ctx = _ctx(state, actor="Player_1")
    registry.execute_effect({"type": "draw_card", "count": 2}, ctx)
    assert state.get_attr("Player_1", "hand") == ["R1", "R2"]

    registry.execute_effect({"type": "play_card", "card": "R1"}, _ctx(state, actor="Player_1"))
    assert state.get_attr("GAME", "top_card") == "R1"
    assert registry.evaluate_condition("cards.hand_empty", {"entity": "Player_1"}, {"state": state}) is False
    registry.execute_effect({"type": "play_card", "card": "R2"}, _ctx(state, actor="Player_1"))
    assert registry.evaluate_condition("cards.hand_empty", {"entity": "Player_1"}, {"state": state}) is True


def test_inventory_grant_use_transfer_and_has_item() -> None:
    """背包：获得/消耗/转移计数型物品 + 拥有判定。"""
    registry = _registry_with(inventory)
    state = _new_state()
    # 运行中动态获得物品
    registry.execute_effect({"type": "grant_item", "item": "heal_potion", "count": 2}, _ctx(state, actor="Player_1"))
    assert state.get_attr("Player_1", "inventory_heal_potion") == 2
    assert registry.evaluate_condition("inventory.has_item", {"entity": "Player_1", "item": "heal_potion"}, {"state": state}) is True

    # 消耗
    registry.execute_effect({"type": "use_item", "item": "heal_potion"}, _ctx(state, actor="Player_1"))
    assert state.get_attr("Player_1", "inventory_heal_potion") == 1

    # 转移给他人
    registry.execute_effect(
        {"type": "transfer_item", "giver": "Player_1", "receiver": "Player_2", "item": "heal_potion"},
        _ctx(state),
    )
    assert state.get_attr("Player_1", "inventory_heal_potion") == 0
    assert state.get_attr("Player_2", "inventory_heal_potion") == 1


def test_inventory_rich_attribute_item() -> None:
    """背包：富属性型物品写入 items dict。"""
    registry = _registry_with(inventory)
    state = _new_state()
    registry.execute_effect(
        {"type": "grant_item", "target": "Player_1", "item": "sword", "attrs": {"atk": 10, "durability": 50}},
        _ctx(state),
    )
    items = state.get_attr("Player_1", "items")
    assert items["sword"]["atk"] == 10
    assert registry.evaluate_condition("inventory.has_item", {"entity": "Player_1", "item": "sword"}, {"state": state}) is True


def test_stats_adjust_attr_and_thresholds() -> None:
    """角色面板：增量修改属性（含好感度）+ 阈值判定。"""
    registry = _registry_with(stats)
    state = _new_state()
    StateWriter(state).apply(SetAttr("Player_1", "hp", 100))

    # 扣血（带下限夹取）
    registry.execute_effect({"type": "adjust_attr", "target": "Player_1", "attr": "hp", "delta": -150, "min": 0}, _ctx(state))
    assert state.get_attr("Player_1", "hp") == 0
    assert registry.evaluate_condition("stats.attr_below", {"entity": "Player_1", "attr": "hp", "value": 1}, {"state": state}) is True

    # 好感度递增（关系型可变属性）
    registry.execute_effect({"type": "adjust_attr", "target": "Player_1", "attr": "affinity_Npc_A", "delta": 3}, _ctx(state))
    registry.execute_effect({"type": "adjust_attr", "target": "Player_1", "attr": "affinity_Npc_A", "delta": 2}, _ctx(state))
    assert state.get_attr("Player_1", "affinity_Npc_A") == 5
    assert registry.evaluate_condition("stats.attr_at_least", {"entity": "Player_1", "attr": "affinity_Npc_A", "value": 5}, {"state": state}) is True


def test_game_pack_registry_registers_all_builtins() -> None:
    """默认运行层注册表应包含全部七个内置机制集合。"""
    registry = build_default_game_pack_runtime_registry()
    for plugin_id in [
        "builtin.board", "builtin.dice", "builtin.economy", "builtin.cards",
        "builtin.social", "builtin.inventory", "builtin.stats",
    ]:
        assert registry.has(plugin_id), f"缺少 {plugin_id}"
    # install 应把机制注册进 plugin registry 并返回默认 config
    plugins = PluginRegistry()
    config = registry.install("builtin.board", PluginApi(plugins))
    assert plugins.has_effect("board_place")
    assert config["board_size"] == 15
    # inventory / stats 也能安装
    registry.install("builtin.inventory", PluginApi(plugins))
    registry.install("builtin.stats", PluginApi(plugins))
    assert plugins.has_effect("grant_item")
    assert plugins.has_effect("adjust_attr")
