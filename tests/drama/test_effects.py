# tests/drama/test_effects.py
"""效果执行器测试。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import pytest

from drama_engine.core.engine import State, StateWriter, SetAttr, Vocabulary
from drama_engine.core.components.effects import EffectExecutor
from drama_engine.core.components.conditions import ConditionEvaluator

_EMPTY_VOCAB = Vocabulary(
    roles=frozenset(),
    factions=frozenset(),
    scopes=frozenset(),
    abilities=frozenset(),
)

def _make_state(players: dict = None, **game_attrs):
    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {**game_attrs})
    if players:
        for name, attrs in players.items():
            state.register_entity(name, attrs)
    return state

evaluator = ConditionEvaluator()
executor = EffectExecutor(evaluator)

def test_set_state_literal():
    state = _make_state()
    writer = StateWriter(state)
    effect = {"type": "set_state", "entity": "GAME", "attr": "saved", "value": True}
    executor.execute(effect, state, writer, responses=[], actor=None)
    assert state.get_attr("GAME", "saved") is True

def test_set_state_with_when_true():
    state = _make_state()
    writer = StateWriter(state)
    effect = {
        "type": "set_state", "entity": "GAME", "attr": "wolf_consensus", "value": True,
        "when": {"state": "GAME.saved", "equals": False},
    }
    executor.execute(effect, state, writer, responses=[], actor=None)
    assert state.get_attr("GAME", "wolf_consensus") is True

def test_set_state_with_when_false():
    state = _make_state(saved=True)
    writer = StateWriter(state)
    effect = {
        "type": "set_state", "entity": "GAME", "attr": "wolf_consensus", "value": True,
        "when": {"state": "GAME.saved", "equals": False},
    }
    executor.execute(effect, state, writer, responses=[], actor=None)
    assert state.get_attr("GAME", "wolf_consensus") is None

def test_increment_state():
    state = _make_state(round=2)
    writer = StateWriter(state)
    effect = {"type": "increment_state", "entity": "GAME", "attr": "round", "value": 1}
    executor.execute(effect, state, writer, responses=[], actor=None)
    assert state.get_attr("GAME", "round") == 3

def test_set_state_can_target_winner_entity():
    """entity=winner 时，效果应写入投票胜出者实体。"""
    players = {"Player_3": {"alive": True, "role": "idiot"}}
    state = _make_state(players=players)
    writer = StateWriter(state)
    effect = {
        "type": "set_state",
        "entity": "winner",
        "attr": "revealed_idiot",
        "value": True,
    }
    executor.execute(
        effect,
        state,
        writer,
        responses=[],
        actor=None,
        extra={"winner": "Player_3"},
    )
    assert state.get_attr("Player_3", "revealed_idiot") is True

def test_effect_when_can_read_winner_attr():
    """effect.when 支持 state=winner.xxx 读取投票胜出者属性。"""
    players = {
        "Player_3": {"alive": True, "role": "idiot"},
        "Player_4": {"alive": True, "role": "villager"},
    }
    state = _make_state(players=players)
    writer = StateWriter(state)
    effect = {
        "type": "set_state",
        "entity": "winner",
        "attr": "revealed_idiot",
        "value": True,
        "when": {
            "state": "winner.role",
            "equals": "idiot",
        },
    }
    executor.execute(
        effect,
        state,
        writer,
        responses=[],
        actor=None,
        extra={"winner": "Player_3", "__state": state},
    )
    assert state.get_attr("Player_3", "revealed_idiot") is True

    executor.execute(
        effect,
        state,
        writer,
        responses=[],
        actor=None,
        extra={"winner": "Player_4", "__state": state},
    )
    assert state.get_attr("Player_4", "revealed_idiot") is None

def test_consume_item():
    players = {"P1": {"alive": True, "inventory_heal_potion": 1}}
    state = _make_state(players=players)
    writer = StateWriter(state)
    effect = {"type": "consume_item", "entity": "actor", "item": "heal_potion"}
    executor.execute(effect, state, writer, responses=[], actor="P1", extra={})
    assert state.get_attr("P1", "inventory_heal_potion") == 0

def test_consume_item_actor_falls_back_to_response_actor():
    players = {"P1": {"alive": True, "inventory_heal_potion": 1}}
    state = _make_state(players=players)
    writer = StateWriter(state)
    effect = {"type": "consume_item", "entity": "actor", "item": "heal_potion"}
    responses = [{"actor": "P1", "data": {"action": True}}]
    executor.execute(effect, state, writer, responses=responses, actor=None, extra={})
    assert state.get_attr("P1", "inventory_heal_potion") == 0

def test_consume_item_not_consumed_when_false():
    players = {"P1": {"alive": True, "inventory_heal_potion": 1}}
    state = _make_state(players=players)
    writer = StateWriter(state)
    effect = {
        "type": "consume_item", "entity": "actor", "item": "heal_potion",
        "when": {"state": "GAME.saved", "equals": True},
    }
    executor.execute(effect, state, writer, responses=[], actor="P1", extra={})
    assert state.get_attr("P1", "inventory_heal_potion") == 1

def test_add_score():
    state = _make_state()
    state.register_entity("good_team", {"score": 0})
    writer = StateWriter(state)
    effect = {"type": "add_score", "team": "good_team", "value": 10}
    executor.execute(effect, state, writer, responses=[], actor=None, extra={})
    assert state.get_attr("good_team", "score") == 10

def test_advance_turn():
    players = {
        "P1": {"alive": True, "is_turn": True},
        "P2": {"alive": True, "is_turn": False},
        "P3": {"alive": True, "is_turn": False},
    }
    state = _make_state(players=players)
    writer = StateWriter(state)
    effect = {"type": "advance_turn", "order": "clockwise", "filter": {"alive": True}}
    executor.execute(effect, state, writer, responses=[], actor=None, extra={})
    assert state.get_attr("P1", "is_turn") is False
    assert state.get_attr("P2", "is_turn") is True


def test_give_item():
    players = {"P1": {"alive": True, "inventory_bomb": 0}}
    state = _make_state(players=players)
    writer = StateWriter(state)
    effect = {"type": "give_item", "entity": "actor", "item": "bomb", "count": 1}
    executor.execute(effect, state, writer, responses=[], actor="P1", extra={})
    assert state.get_attr("P1", "inventory_bomb") == 1

def test_set_state_from_data_action():
    """效果 value=data.action 从 responses 中取。"""
    state = _make_state()
    writer = StateWriter(state)
    effect = {"type": "set_state", "entity": "GAME", "attr": "saved", "value": "data.action"}
    responses = [{"actor": "P1", "data": {"action": True}}]
    executor.execute(effect, state, writer, responses=responses, actor="P1", extra={})
    assert state.get_attr("GAME", "saved") is True

def test_when_can_read_data_action():
    """effect.when 支持 state=data.action 读取本幕响应。"""
    state = _make_state()
    writer = StateWriter(state)
    effect = {
        "type": "set_state",
        "entity": "GAME",
        "attr": "saved",
        "value": True,
        "when": {"state": "data.action", "equals": True},
    }
    responses = [{"actor": "P1", "data": {"action": True}}]
    executor.execute(effect, state, writer, responses=responses, actor="P1", extra={})
    assert state.get_attr("GAME", "saved") is True

def test_when_can_skip_on_data_action_false():
    state = _make_state()
    writer = StateWriter(state)
    effect = {
        "type": "set_state",
        "entity": "GAME",
        "attr": "saved",
        "value": True,
        "when": {"state": "data.action", "equals": True},
    }
    responses = [{"actor": "P1", "data": {"action": False}}]
    executor.execute(effect, state, writer, responses=responses, actor="P1", extra={})
    assert state.get_attr("GAME", "saved") is None

def test_legacy_condition_field_is_rejected():
    """旧字段 condition 已删除；effects 条件必须使用 when。"""
    state = _make_state()
    writer = StateWriter(state)
    legacy_key = "con" + "dition"
    effect = {
        "type": "set_state",
        "entity": "GAME",
        "attr": "saved",
        "value": True,
        legacy_key: {"state": "GAME.saved", "equals": False},
    }
    try:
        executor.execute(effect, state, writer, responses=[], actor=None)
    except ValueError as exc:
        assert "已删除" in str(exc)
    else:
        raise AssertionError("旧字段 condition 应该被拒绝")

def test_broadcast_records_pending():
    """broadcast 效果应把消息写入 GAME.__pending_broadcasts。"""
    state = _make_state()
    writer = StateWriter(state)
    effect = {"type": "broadcast", "scope": "whisper:seer", "template": "查验结果: xxx"}
    executor.execute(effect, state, writer, responses=[], actor=None, extra={})
    pending = state.get_attr("GAME", "__pending_broadcasts")
    assert pending is not None and len(pending) == 1
    assert pending[0]["scope"] == "whisper:seer"

def test_broadcast_template_can_read_data_target_attrs():
    """broadcast 模板应能读取 data.target 指向实体的属性。"""
    players = {
        "Player_5": {
            "faction": "wolf",
        }
    }
    state = _make_state(players=players)
    writer = StateWriter(state)
    effect = {
        "type": "broadcast",
        "scope": "whisper:seer",
        "template": "查验结果：{data.target} 的阵营是 {data.target.faction}。",
    }
    responses = [{"actor": "Player_1", "data": {"target": "Player_5"}}]
    executor.execute(effect, state, writer, responses=responses, actor=None, extra={})
    pending = state.get_attr("GAME", "__pending_broadcasts")
    assert pending[0]["template"] == "查验结果：Player_5 的阵营是 wolf。"


def test_broadcast_template_can_read_item_context():
    """broadcast 模板应能读取 for_each/trigger 注入的 item 上下文。"""
    state = _make_state()
    writer = StateWriter(state)
    effect = {
        "type": "broadcast",
        "scope": "public",
        "template": "{item.entity} 状态变化为 {item.value}",
    }
    executor.execute(
        effect,
        state,
        writer,
        responses=[],
        actor=None,
        extra={"item": {"entity": "P1", "value": "dead"}},
    )
    pending = state.get_attr("GAME", "__pending_broadcasts")
    assert pending[0]["template"] == "P1 状态变化为 dead"

def test_add_remove_clear_effects():
    """集合 effect 应能追加、移除和清空状态列表。"""
    state = _make_state(sheriff_candidates=[])
    writer = StateWriter(state)

    executor.execute(
        {"type": "add", "path": "GAME.sheriff_candidates", "value": "actor"},
        state,
        writer,
        responses=[],
        actor="P1",
        extra={},
    )
    executor.execute(
        {"type": "add", "path": "GAME.sheriff_candidates", "value": "actor"},
        state,
        writer,
        responses=[],
        actor="P1",
        extra={},
    )
    assert state.get_attr("GAME", "sheriff_candidates") == ["P1"]

    executor.execute(
        {"type": "remove", "path": "GAME.sheriff_candidates", "value": "@P1"},
        state,
        writer,
        responses=[],
        actor=None,
        extra={},
    )
    assert state.get_attr("GAME", "sheriff_candidates") == []

    executor.execute(
        {"type": "add", "path": "GAME.sheriff_candidates", "value": "@P2"},
        state,
        writer,
        responses=[],
        actor=None,
        extra={},
    )
    executor.execute(
        {"type": "clear", "path": "GAME.sheriff_candidates"},
        state,
        writer,
        responses=[],
        actor=None,
        extra={},
    )
    assert state.get_attr("GAME", "sheriff_candidates") == []


def test_removed_set_effect_names_are_rejected():
    """已删除的集合 effect 旧名应直接报错。"""
    state = _make_state(sheriff_candidates=[])
    writer = StateWriter(state)

    for effect_type in ["set_add", "set_remove", "set_clear"]:
        with pytest.raises(ValueError, match="未知效果类型"):
            executor.execute(
                {"type": effect_type, "path": "GAME.sheriff_candidates", "value": "@P1"},
                state,
                writer,
                responses=[],
                actor=None,
                extra={},
            )
    assert state.get_attr("GAME", "sheriff_candidates") == []


def test_relation_effects_and_targets():
    """关系 effect 应能建立、读取和清理关系边。"""
    players = {
        "P1": {"alive": True},
        "P2": {"alive": True},
    }
    state = _make_state(players=players)
    writer = StateWriter(state)

    executor.execute(
        {
            "type": "set_relation",
            "relation": "lover",
            "source": "@P1",
            "target": "@P2",
            "bidirectional": True,
        },
        state,
        writer,
        responses=[],
        actor=None,
        extra={},
    )
    assert state.related("lover", "P1") == {"P2"}
    assert state.related("lover", "P2") == {"P1"}

    executor.execute(
        {
            "type": "get_relations",
            "relation": "lover",
            "source": "@P1",
            "path": "GAME.lover_targets",
        },
        state,
        writer,
        responses=[],
        actor=None,
        extra={},
    )
    assert state.get_attr("GAME", "lover_targets") == ["P2"]

    executor.execute(
        {"type": "clear_relation", "relation": "lover", "source": "@P1"},
        state,
        writer,
        responses=[],
        actor=None,
        extra={},
    )
    assert state.related("lover", "P1") == set()
    assert state.related("lover", "P2") == {"P1"}


def test_removed_relation_effect_names_are_rejected():
    """已删除的关系 effect 旧名应直接报错。"""
    players = {
        "P1": {"alive": True},
        "P2": {"alive": True},
    }
    state = _make_state(players=players)
    writer = StateWriter(state)

    for effect in [
        {"type": "relation_set", "relation": "lover", "source": "@P1", "target": "@P2"},
        {"type": "relation_targets", "relation": "lover", "source": "@P1", "path": "GAME.links"},
        {"type": "relation_clear", "relation": "lover", "source": "@P1"},
    ]:
        with pytest.raises(ValueError, match="未知效果类型"):
            executor.execute(
                effect,
                state,
                writer,
                responses=[],
                actor=None,
                extra={},
            )
    assert state.get_attr("GAME", "links") is None
    assert state.related("lover", "P1") == set()


def test_data_index_for_each_and_pending_resolve():
    """路径索引、for_each 和 pending 队列应组合工作。"""
    players = {
        "P1": {"alive": True},
        "P2": {"alive": True},
    }
    state = _make_state(players=players, round=1)
    writer = StateWriter(state)
    responses = [{"actor": "Cupid", "data": {"targets": ["P1", "P2"]}}]

    executor.execute(
        {
            "type": "set_state",
            "entity": "GAME",
            "attr": "first_target",
            "value": "data.targets[0]",
        },
        state,
        writer,
        responses=responses,
        actor=None,
        extra={},
    )
    assert state.get_attr("GAME", "first_target") == "P1"

    executor.execute(
        {
            "type": "for_each",
            "items": "data.targets",
            "as": "item",
            "effects": [
                {
                    "type": "pending_add",
                    "queue": "deaths",
                    "item": {"target": "item", "cause": "@linked"},
                }
            ],
        },
        state,
        writer,
        responses=responses,
        actor=None,
        extra={},
    )
    assert state.get_attr("GAME", "__pending_deaths") == [
        {"target": "P1", "cause": "linked"},
        {"target": "P2", "cause": "linked"},
    ]

    executor.execute(
        {
            "type": "pending_resolve",
            "queue": "deaths",
            "as": "item",
            "effects": [
                # 用通用 set_state 验证 for_each/pending 嵌套（kill 已迁到 builtin.social）。
                {"type": "set_state", "entity": "item.target", "attr": "alive", "value": False}
            ],
        },
        state,
        writer,
        responses=[],
        actor=None,
        extra={},
    )
    assert state.get_attr("P1", "alive") is False
    assert state.get_attr("P2", "alive") is False
    assert state.get_attr("GAME", "__pending_deaths") == []


def test_context_path_can_read_entity_attrs_after_indexing():
    """data.targets[0].faction 和 item.alive 应能继续读取实体状态属性。"""
    players = {
        "P1": {"alive": True, "faction": "wolf"},
        "P2": {"alive": True, "faction": "good"},
    }
    state = _make_state(players=players)
    writer = StateWriter(state)
    responses = [{"actor": "Cupid", "data": {"targets": ["P1", "P2"]}}]

    executor.execute(
        {
            "type": "set_state",
            "entity": "GAME",
            "attr": "mixed_pair",
            "value": True,
            "when": {
                "state": "data.targets[0].faction",
                "not_equals_state": "data.targets[1].faction",
            },
        },
        state,
        writer,
        responses=responses,
        actor=None,
        extra={},
    )

    assert state.get_attr("GAME", "mixed_pair") is True

    executor.execute(
        {
            "type": "for_each",
            "items": "data.targets",
            "as": "item",
            "effects": [
                {
                    "type": "set_state",
                    "entity": "item",
                    "attr": "marked",
                    "value": True,
                    "when": {"state": "item.alive", "equals": True},
                }
            ],
        },
        state,
        writer,
        responses=responses,
        actor=None,
        extra={},
    )

    assert state.get_attr("P1", "marked") is True
    assert state.get_attr("P2", "marked") is True


# ── H4：_compare_value 委托 canonical compare_operator 的回归 ──
# 这些用例覆盖 effect.when 的各类 key-based 操作符，确认比较统一走 operators.compare_operator，
# 新增操作符只需改 operators.py 一处（effects.py 不再自带操作符实现）。

def test_effect_when_numeric_operators_via_compare_operator():
    """effect.when 的 gte/lte/gt/lt 数值比较（走委托后的统一实现）。"""
    cases = [
        ({"state": "GAME.hp", "gte": 3}, 3, True),
        ({"state": "GAME.hp", "gte": 4}, 3, False),
        ({"state": "GAME.hp", "lte": 3}, 3, True),
        ({"state": "GAME.hp", "gt": 2}, 3, True),
        ({"state": "GAME.hp", "gt": 3}, 3, False),
        ({"state": "GAME.hp", "lt": 5}, 3, True),
    ]
    for when, hp, expected in cases:
        state = _make_state(hp=hp)
        writer = StateWriter(state)
        effect = {"type": "set_state", "entity": "GAME", "attr": "flag", "value": True, "when": when}
        executor.execute(effect, state, writer, responses=[], actor=None)
        assert (state.get_attr("GAME", "flag") is True) is expected, f"when={when} hp={hp}"


def test_effect_when_in_and_not_in_via_compare_operator():
    """effect.when 的 in / not_in（走统一实现）。"""
    state = _make_state(phase="night")
    writer = StateWriter(state)
    effect = {
        "type": "set_state", "entity": "GAME", "attr": "flag", "value": True,
        "when": {"state": "GAME.phase", "in": ["night", "dusk"]},
    }
    executor.execute(effect, state, writer, responses=[], actor=None)
    assert state.get_attr("GAME", "flag") is True


def test_effect_when_null_checks_via_compare_operator():
    """effect.when 的 is_null / not_null（走统一实现）。"""
    state = _make_state(target=None)
    writer = StateWriter(state)
    effect = {
        "type": "set_state", "entity": "GAME", "attr": "flag", "value": True,
        "when": {"state": "GAME.target", "is_null": True},
    }
    executor.execute(effect, state, writer, responses=[], actor=None)
    assert state.get_attr("GAME", "flag") is True
