"""机制库单元测试。

直接以 EffectContext / condition context 驱动各机制，验证其状态读写正确，
不依赖完整 runtime。
"""

from __future__ import annotations

from drama_engine.core.engine import SetAttr, State, StateWriter, Vocabulary
from drama_engine.core.components import EffectExecutor  # noqa: F401 - 先加载组件，避免循环导入
from drama_engine.core.plugins import EffectContext, PluginApi, PluginRegistry
from drama_engine.core.game_packs import build_default_game_pack_runtime_registry
from drama_engine.core.game_packs.mechanisms import (
    affinity,
    board,
    cards,
    dice,
    economy,
    inventory,
    narrative,
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


# ── M1-B：从通用 DSL 层迁入 builtin.social 的狼人杀专属机制 ──

def test_social_kill_sets_alive_cause_round() -> None:
    """social.kill 应设 alive=False + death_cause + death_round。"""
    state = _new_state(players=2)
    StateWriter(state).apply(SetAttr("GAME", "round", 3))
    registry = _registry_with(social)
    registry.execute_effect(
        {"type": "social.kill", "target": "@Player_1", "cause": "wolf"},
        _ctx(state),
    )
    assert state.get_attr("Player_1", "alive") is False
    assert state.get_attr("Player_1", "death_cause") == "wolf"
    assert state.get_attr("Player_1", "death_round") == 3


def test_social_record_target_writes_game_attr() -> None:
    """social.record_target 应把来源实体写入 GAME 指定属性。"""
    state = _new_state(players=2)
    registry = _registry_with(social)
    registry.execute_effect(
        {"type": "social.record_target", "attr": "night_target", "source": "@Player_2"},
        _ctx(state),
    )
    assert state.get_attr("GAME", "night_target") == "Player_2"


def test_social_record_current_deaths_by_round_and_seat_order() -> None:
    """social.record_current_deaths 记录本轮死亡（按座位排序）。"""
    state = _new_state(players=3)
    writer = StateWriter(state)
    writer.apply(SetAttr("GAME", "round", 2))
    writer.apply(SetAttr("Player_2", "death_round", 2))
    writer.apply(SetAttr("Player_3", "death_round", 2))
    writer.apply(SetAttr("Player_1", "death_round", 1))  # 上一轮死的不计入
    registry = _registry_with(social)
    registry.execute_effect(
        {"type": "social.record_current_deaths", "path": "GAME.today_deaths"},
        _ctx(state),
    )
    assert state.get_attr("GAME", "today_deaths") == ["Player_2", "Player_3"]


def test_social_build_speech_order_left_from_death() -> None:
    """social.build_speech_order 从参考点按方向生成发言顺序。"""
    state = _new_state(players=3)
    StateWriter(state).apply(SetAttr("GAME", "round", 2))
    registry = _registry_with(social)
    registry.execute_effect(
        {
            "type": "social.build_speech_order",
            "path": "GAME.speech_order",
            "reference": "@Player_1",
            "direction": "left",
            "filter": {"alive": True},
        },
        _ctx(state),
    )
    order = state.get_attr("GAME", "speech_order")
    assert order == ["Player_2", "Player_3", "Player_1"]


def test_social_just_died_condition() -> None:
    """social.just_died 判断实体是否本轮死亡。"""
    state = _new_state(players=2)
    StateWriter(state).apply(SetAttr("GAME", "round", 2))
    StateWriter(state).apply(SetAttr("Player_1", "death_round", 2))
    registry = _registry_with(social)
    assert registry.evaluate_condition("social.just_died", {"entity": "Player_1"}, {"state": state}) is True
    assert registry.evaluate_condition("social.just_died", {"entity": "Player_2"}, {"state": state}) is False


def test_social_is_first_round_condition() -> None:
    """social.is_first_round 判断是否首轮。"""
    registry = _registry_with(social)
    state = _new_state(players=2)
    StateWriter(state).apply(SetAttr("GAME", "round", 1))
    assert registry.evaluate_condition("social.is_first_round", {}, {"state": state}) is True
    StateWriter(state).apply(SetAttr("GAME", "round", 2))
    assert registry.evaluate_condition("social.is_first_round", {}, {"state": state}) is False


# ============ narrative 机制 ============

def test_narrative_record_choice_appends_history_and_visited() -> None:
    """record_choice 追加选择历史并计入 visited_nodes（去重）。"""
    registry = _registry_with(narrative)
    state = _new_state(players=1)
    registry.execute_effect(
        {"type": "record_choice", "node": "intro", "choice": "enter"}, _ctx(state)
    )
    registry.execute_effect(
        {"type": "record_choice", "node": "intro", "choice": "again"}, _ctx(state)
    )
    assert state.get_attr("GAME", "choice_history") == [
        {"node": "intro", "choice": "enter"},
        {"node": "intro", "choice": "again"},
    ]
    # 同一节点只在 visited_nodes 出现一次
    assert state.get_attr("GAME", "visited_nodes") == ["intro"]


def test_narrative_collect_clue_dedup_and_condition() -> None:
    """collect_clue 去重加入线索；narrative.clue_collected 判定已搜集。"""
    registry = _registry_with(narrative)
    state = _new_state(players=1)
    registry.execute_effect({"type": "collect_clue", "clue": "letter"}, _ctx(state))
    registry.execute_effect({"type": "collect_clue", "clue": "letter"}, _ctx(state))
    assert state.get_attr("GAME", "clues") == ["letter"]
    assert registry.evaluate_condition(
        "narrative.clue_collected", {"clue": "letter"}, {"state": state}
    ) is True
    assert registry.evaluate_condition(
        "narrative.clue_collected", {"clue": "knife"}, {"state": state}
    ) is False


def test_narrative_set_ending_by_rules() -> None:
    """set_ending 按 rules 阈值选定结局；reached_ending 判定。"""
    registry = _registry_with(narrative)
    state = _new_state(players=1)
    StateWriter(state).apply(SetAttr("GAME", "affection", 6))
    registry.execute_effect(
        {
            "type": "set_ending",
            "rules": [
                {"ending": "good_end", "attr": "GAME.affection", "at_least": 5},
                {"ending": "bad_end", "attr": "GAME.affection", "below": 5},
            ],
        },
        _ctx(state),
    )
    assert state.get_attr("GAME", "ending") == "good_end"
    assert registry.evaluate_condition("narrative.reached_ending", {}, {"state": state}) is True
    assert registry.evaluate_condition(
        "narrative.reached_ending", {"ending": "bad_end"}, {"state": state}
    ) is False


def test_narrative_set_ending_default_fallback() -> None:
    """无 rule 命中时用 default 兜底。"""
    registry = _registry_with(narrative)
    state = _new_state(players=1)
    StateWriter(state).apply(SetAttr("GAME", "affection", 1))
    registry.execute_effect(
        {
            "type": "set_ending",
            "rules": [{"ending": "good_end", "attr": "GAME.affection", "at_least": 5}],
            "default": "normal_end",
        },
        _ctx(state),
    )
    assert state.get_attr("GAME", "ending") == "normal_end"


# ============ affinity 机制 ============

def test_affinity_set_and_mutual_condition() -> None:
    """set_affinity 设绝对值；affinity.mutual_at_least 判互相达标。"""
    registry = _registry_with(affinity)
    state = _new_state(players=2)
    registry.execute_effect(
        {"type": "set_affinity", "source": "Player_1", "target": "Player_2", "value": 5}, _ctx(state)
    )
    # 单向达标不算 mutual
    assert registry.evaluate_condition(
        "affinity.mutual_at_least",
        {"a": "Player_1", "b": "Player_2", "value": 5},
        {"state": state},
    ) is False
    registry.execute_effect(
        {"type": "set_affinity", "source": "Player_2", "target": "Player_1", "value": 6}, _ctx(state)
    )
    assert registry.evaluate_condition(
        "affinity.mutual_at_least",
        {"a": "Player_1", "b": "Player_2", "value": 5},
        {"state": state},
    ) is True


def test_affinity_pair_by_affinity() -> None:
    """pair_by_affinity 按互相好感之和贪心配对。"""
    registry = _registry_with(affinity)
    state = _new_state(players=4)
    writer = StateWriter(state)
    # 1<->2 互相高好感，3<->4 次之
    writer.apply(SetAttr("Player_1", "affinity_Player_2", 9))
    writer.apply(SetAttr("Player_2", "affinity_Player_1", 9))
    writer.apply(SetAttr("Player_3", "affinity_Player_4", 4))
    writer.apply(SetAttr("Player_4", "affinity_Player_3", 4))
    registry.execute_effect({"type": "pair_by_affinity"}, _ctx(state))
    pairs = state.get_attr("GAME", "pairs")
    assert ["Player_1", "Player_2"] in pairs
    assert ["Player_3", "Player_4"] in pairs
    assert len(pairs) == 2


def test_affinity_eliminate_lowest() -> None:
    """eliminate_lowest 淘汰收到总好感最低者。"""
    registry = _registry_with(affinity)
    state = _new_state(players=3)
    writer = StateWriter(state)
    # Player_1 收到高好感，Player_2 收到一点，Player_3 无人喜欢（唯一最低）
    writer.apply(SetAttr("Player_2", "affinity_Player_1", 8))
    writer.apply(SetAttr("Player_3", "affinity_Player_1", 7))
    writer.apply(SetAttr("Player_1", "affinity_Player_2", 2))
    registry.execute_effect({"type": "eliminate_lowest"}, _ctx(state))
    assert state.get_attr("Player_3", "eliminated") is True
    assert state.get_attr("GAME", "last_eliminated") == "Player_3"
    assert state.get_attr("Player_1", "eliminated") is not True


def test_new_packs_registered_in_runtime_registry() -> None:
    """builtin.narrative / builtin.affinity 应注册进运行层 GamePack 注册表。"""
    registry = build_default_game_pack_runtime_registry()
    assert registry.has("builtin.narrative")
    assert registry.has("builtin.affinity")
    # 机制名进入 manifest，供编译期 effect 白名单校验
    assert "record_choice" in registry.get("builtin.narrative").mechanisms
    assert "pair_by_affinity" in registry.get("builtin.affinity").mechanisms
