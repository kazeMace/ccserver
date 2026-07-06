"""机制库单元测试。

直接以 EffectContext / condition context 驱动各机制，验证其状态读写正确，
不依赖完整 runtime。
"""

from __future__ import annotations

from drama_engine.core.engine import SetAttr, State, StateWriter, Vocabulary
from drama_engine.core.dsl.components import EffectExecutor  # noqa: F401 - 先加载组件，避免循环导入
from drama_engine.core.dsl.plugins import EffectContext, PluginApi, PluginRegistry
from drama_engine.core.game_packs import build_default_game_pack_runtime_registry
from drama_engine.core.game_packs.mechanisms import board, cards, dice, economy, social


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


def test_game_pack_registry_registers_all_builtins() -> None:
    """默认运行层注册表应包含五个内置机制集合。"""
    registry = build_default_game_pack_runtime_registry()
    for plugin_id in ["builtin.board", "builtin.dice", "builtin.economy", "builtin.cards", "builtin.social"]:
        assert registry.has(plugin_id), f"缺少 {plugin_id}"
    # install 应把机制注册进 plugin registry 并返回默认 config
    plugins = PluginRegistry()
    config = registry.install("builtin.board", PluginApi(plugins))
    assert plugins.has_effect("board_place")
    assert config["board_size"] == 15
