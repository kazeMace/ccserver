# tests/drama/test_conditions.py
"""条件原语求值测试。"""

import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from drama_engine.core.engine import State, StateWriter, SetAttr, Vocabulary
from drama_engine.core.components.conditions import ConditionEvaluator

# 测试用最小词汇表，不做任何词汇校验（空集合）
_EMPTY_VOCAB = Vocabulary(
    roles=frozenset(),
    factions=frozenset(),
    scopes=frozenset(),
    abilities=frozenset(),
)


def _make_state(**game_attrs):
    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {})
    for k, v in game_attrs.items():
        w = StateWriter(state)
        w.apply(SetAttr("GAME", k, v))
    return state


def _make_state_with_players(players: dict):
    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {})
    for name, attrs in players.items():
        state.register_entity(name, attrs)
    return state


evaluator = ConditionEvaluator()


def test_equals_true():
    state = _make_state(saved=True)
    assert evaluator.evaluate({"state": "GAME.saved", "equals": True}, state, actor=None) is True

def test_equals_false():
    state = _make_state(saved=False)
    assert evaluator.evaluate({"state": "GAME.saved", "equals": True}, state, actor=None) is False

def test_is_null_true():
    state = _make_state(wolf_target=None)
    assert evaluator.evaluate({"state": "GAME.wolf_target", "is_null": True}, state, actor=None) is True

def test_not_null_true():
    state = _make_state(wolf_target="Player_1")
    assert evaluator.evaluate({"state": "GAME.wolf_target", "not_null": True}, state, actor=None) is True

def test_gte():
    state = _make_state(round=3)
    assert evaluator.evaluate({"state": "GAME.round", "gte": 2}, state, actor=None) is True
    assert evaluator.evaluate({"state": "GAME.round", "gte": 4}, state, actor=None) is False

def test_lte():
    state = _make_state(round=3)
    assert evaluator.evaluate({"state": "GAME.round", "lte": 3}, state, actor=None) is True
    assert evaluator.evaluate({"state": "GAME.round", "lte": 2}, state, actor=None) is False

def test_in_list():
    players = {"Player_1": {"alive": True, "role": "seer"}}
    state = _make_state_with_players(players)
    assert evaluator.evaluate({"state": "Player_1.role", "in": ["hunter", "seer"]}, state, actor="Player_1") is True

def test_not_in_list():
    players = {"Player_1": {"alive": True, "role": "werewolf"}}
    state = _make_state_with_players(players)
    assert evaluator.evaluate({"state": "Player_1.role", "not_in": ["hunter", "seer"]}, state, actor="Player_1") is True

def test_count_equals():
    players = {
        "P1": {"alive": True, "faction": "wolf"},
        "P2": {"alive": True, "faction": "wolf"},
        "P3": {"alive": True, "faction": "good"},
    }
    state = _make_state_with_players(players)
    cond = {"count": {"filter": {"alive": True, "faction": "wolf"}}, "equals": 2}
    assert evaluator.evaluate(cond, state, actor=None) is True

def test_count_gte_than_another_count():
    players = {
        "P1": {"alive": True, "faction": "wolf"},
        "P2": {"alive": True, "faction": "wolf"},
        "P3": {"alive": True, "faction": "good"},
        "P4": {"alive": True, "faction": "good"},
    }
    state = _make_state_with_players(players)
    cond = {
        "count": {"filter": {"alive": True, "faction": "wolf"}},
        "gte_than": {"count": {"filter": {"alive": True, "faction": "good"}}},
    }
    assert evaluator.evaluate(cond, state, actor=None) is True

def test_count_not_gte_than():
    players = {
        "P1": {"alive": True, "faction": "wolf"},
        "P2": {"alive": True, "faction": "good"},
        "P3": {"alive": True, "faction": "good"},
    }
    state = _make_state_with_players(players)
    cond = {
        "count": {"filter": {"alive": True, "faction": "wolf"}},
        "gte_than": {"count": {"filter": {"alive": True, "faction": "good"}}},
    }
    assert evaluator.evaluate(cond, state, actor=None) is False

def test_equals_state():
    state = _make_state(accused="Player_3", real_killer="Player_3")
    assert evaluator.evaluate({"state": "GAME.accused", "equals_state": "GAME.real_killer"}, state, actor=None) is True

def test_equals_state_false():
    state = _make_state(accused="Player_1", real_killer="Player_3")
    assert evaluator.evaluate({"state": "GAME.accused", "equals_state": "GAME.real_killer"}, state, actor=None) is False

def test_all_true():
    state = _make_state(saved=False, wolf_target="Player_1")
    cond = {"all": [
        {"state": "GAME.saved", "equals": False},
        {"state": "GAME.wolf_target", "not_null": True},
    ]}
    assert evaluator.evaluate(cond, state, actor=None) is True

def test_all_false_if_one_fails():
    state = _make_state(saved=True, wolf_target="Player_1")
    cond = {"all": [
        {"state": "GAME.saved", "equals": False},
        {"state": "GAME.wolf_target", "not_null": True},
    ]}
    assert evaluator.evaluate(cond, state, actor=None) is False

def test_any_true_if_one_passes():
    state = _make_state(saved=True, wolf_target=None)
    cond = {"any": [
        {"state": "GAME.saved", "equals": False},
        {"state": "GAME.wolf_target", "is_null": True},
    ]}
    assert evaluator.evaluate(cond, state, actor=None) is True

def test_not():
    state = _make_state(saved=False)
    assert evaluator.evaluate({"not": {"state": "GAME.saved", "equals": True}}, state, actor=None) is True

def test_item_available_true():
    players = {"P1": {"alive": True, "inventory_heal_potion": 1}}
    state = _make_state_with_players(players)
    assert evaluator.evaluate({"item_available": {"entity": "P1", "item": "heal_potion"}}, state, actor="P1") is True

def test_item_available_false_when_zero():
    players = {"P1": {"alive": True, "inventory_heal_potion": 0}}
    state = _make_state_with_players(players)
    assert evaluator.evaluate({"item_available": {"entity": "P1", "item": "heal_potion"}}, state, actor="P1") is False

def test_actor_keyword_in_state_path():
    players = {"Player_2": {"alive": True, "role": "witch"}}
    state = _make_state_with_players(players)
    assert evaluator.evaluate({"state": "actor.role", "equals": "witch"}, state, actor="Player_2") is True

def test_candidate_keyword_in_state_path():
    players = {"Player_5": {"alive": True, "role": "seer"}}
    state = _make_state_with_players(players)
    assert evaluator.evaluate(
        {"state": "candidate.role", "equals": "seer"},
        state,
        actor="Player_1",
        candidate="Player_5",
    ) is True

def test_state_candidate_can_compare_to_actor():
    players = {
        "Player_1": {"alive": True},
        "Player_2": {"alive": True},
    }
    state = _make_state_with_players(players)
    cond = {"state": "candidate", "not_equals_state": "actor"}
    assert evaluator.evaluate(cond, state, actor="Player_1", candidate="Player_2") is True
    assert evaluator.evaluate(cond, state, actor="Player_1", candidate="Player_1") is False

def test_not_equals():
    state = _make_state(saved=True)
    assert evaluator.evaluate({"state": "GAME.saved", "not_equals": False}, state, actor=None) is True

def test_gt():
    state = _make_state(round=3)
    assert evaluator.evaluate({"state": "GAME.round", "gt": 2}, state, actor=None) is True
    assert evaluator.evaluate({"state": "GAME.round", "gt": 3}, state, actor=None) is False

def test_lt():
    state = _make_state(round=3)
    assert evaluator.evaluate({"state": "GAME.round", "lt": 4}, state, actor=None) is True
    assert evaluator.evaluate({"state": "GAME.round", "lt": 3}, state, actor=None) is False

def test_item_available_unlimited():
    players = {"P1": {"alive": True, "inventory_wolf_vote": "unlimited"}}
    state = _make_state_with_players(players)
    assert evaluator.evaluate({"item_available": {"entity": "P1", "item": "wolf_vote"}}, state, actor="P1") is True

def test_item_available_none():
    players = {"P1": {"alive": True}}
    state = _make_state_with_players(players)
    assert evaluator.evaluate({"item_available": {"entity": "P1", "item": "heal_potion"}}, state, actor="P1") is False

def test_filter_entities():
    players = {
        "P1": {"alive": True, "faction": "wolf"},
        "P2": {"alive": True, "faction": "good"},
        "P3": {"alive": False, "faction": "wolf"},
    }
    state = _make_state_with_players(players)
    result = evaluator.filter_entities({"alive": True, "faction": "wolf"}, state)
    assert result == {"P1"}


def test_filter_entities_supports_value_condition():
    players = {
        "P1": {"alive": True, "faction": "wolf", "vote_weight": 1},
        "P2": {"alive": True, "faction": "good", "vote_weight": 0},
        "P3": {"alive": False, "faction": "wolf", "vote_weight": 1},
    }
    state = _make_state_with_players(players)
    result = evaluator.filter_entities(
        {
            "all": [
                {"value": "alive", "equal": True},
                {"value": "vote_weight", "greater_than": 0},
            ]
        },
        state,
    )
    assert result == {"P1"}


def test_filter_entities_supports_explicit_entity_ref():
    players = {
        "P1": {"alive": True, "role": "werewolf"},
        "P2": {"alive": True, "role": "seer"},
    }
    state = _make_state_with_players(players)
    result = evaluator.filter_entities(
        {
            "all": [
                {"value": {"ref": "entity.alive"}, "equal": True},
                {"value": {"ref": "entity.role"}, "equal": "werewolf"},
            ]
        },
        state,
    )
    assert result == {"P1"}


def test_when_value_ref_comparisons():
    players = {
        "P1": {"alive": True, "faction": "good"},
        "P2": {"alive": True, "faction": "wolf"},
    }
    state = _make_state_with_players(players)
    writer = StateWriter(state)
    writer.apply(SetAttr("GAME", "round", 2))
    assert evaluator.evaluate(
        {"value": {"ref": "GAME.round"}, "greater_than_equal": 2},
        state,
        actor=None,
    ) is True
    assert evaluator.evaluate(
        {"value": {"ref": "candidate.faction"}, "not_equal": {"ref": "actor.faction"}},
        state,
        actor="P1",
        candidate="P2",
    ) is True


def test_unified_ref_op_condition():
    """ref/op/value 是新的统一 condition 写法。"""
    state = _make_state(round=3)

    assert evaluator.evaluate(
        {"ref": "GAME.round", "op": "greater_than_equal", "value": 3},
        state,
        actor=None,
    ) is True
    assert evaluator.evaluate(
        {"executor": "primitive", "ref": "GAME.round", "op": "less_than", "value": 3},
        state,
        actor=None,
    ) is False


def test_unified_left_op_right_condition_with_count():
    """left/op/right 支持结构化 value expression，例如 count。"""
    players = {
        "P1": {"alive": True, "faction": "wolf"},
        "P2": {"alive": False, "faction": "wolf"},
        "P3": {"alive": True, "faction": "good"},
    }
    state = _make_state_with_players(players)

    assert evaluator.evaluate(
        {
            "left": {
                "count": {
                    "filter": {
                        "all": [
                            {"value": "alive", "equal": True},
                            {"value": "faction", "equal": "wolf"},
                        ]
                    }
                }
            },
            "op": "equal",
            "right": 1,
        },
        state,
        actor=None,
    ) is True


def test_canonical_left_right_supports_plain_ref_strings():
    """left/right 是统一比较语法，裸路径字符串按 ref 解析。"""
    players = {
        "P1": {"alive": True, "faction": "good"},
        "P2": {"alive": True, "faction": "wolf"},
    }
    state = _make_state_with_players(players)
    writer = StateWriter(state)
    writer.apply(SetAttr("GAME", "round", 3))

    assert evaluator.evaluate(
        {"left": "GAME.round", "op": "greater_than_equal", "right": 2},
        state,
        actor=None,
    ) is True
    assert evaluator.evaluate(
        {"left": "candidate.faction", "op": "not_equal", "right": "actor.faction"},
        state,
        actor="P1",
        candidate="P2",
    ) is True


def test_canonical_left_right_supports_count_on_both_sides():
    """left/right 两侧都可以使用 count value expression。"""
    players = {
        "P1": {"alive": True, "faction": "wolf"},
        "P2": {"alive": True, "faction": "wolf"},
        "P3": {"alive": True, "faction": "good"},
    }
    state = _make_state_with_players(players)

    assert evaluator.evaluate(
        {
            "left": {"count": {"filter": {"left": "faction", "op": "equal", "right": "wolf"}}},
            "op": "greater_than",
            "right": {"count": {"filter": {"left": "faction", "op": "equal", "right": "good"}}},
        },
        state,
        actor=None,
    ) is True


def test_canonical_left_right_literal_escape():
    """@ 前缀用于在比较语境中强制表达字符串字面量。"""
    state = _make_state(target="GAME.round")

    assert evaluator.evaluate(
        {"left": "GAME.target", "op": "equal", "right": "@GAME.round"},
        state,
        actor=None,
    ) is True


def test_code_evaluator_python_with_env():
    """code evaluator 支持指定 runtime/env/code。"""
    state = _make_state(round=4)

    assert evaluator.evaluate(
        {
            "executor": "code",
            "runtime": "python",
            "env": {"MIN_ROUND": "3"},
            "code": "result = state('GAME.round') >= int(env('MIN_ROUND'))",
        },
        state,
        actor=None,
    ) is True


def test_code_evaluator_shell_exit_code():
    """shell code evaluator 使用退出码作为布尔结果。"""
    state = _make_state(round=1)

    assert evaluator.evaluate(
        {
            "executor": "code",
            "runtime": "shell",
            "code": "python - <<'PY'\nimport json, os, sys\nctx=json.loads(os.environ['DRAMA_CONDITION_CONTEXT'])\nsys.exit(0 if ctx['state']['GAME']['round'] == 1 else 1)\nPY",
        },
        state,
        actor=None,
    ) is True


def test_http_evaluator_without_endpoint_uses_fallback():
    """http/llm evaluator 未配置 endpoint 时应使用 fallback。"""
    state = _make_state(round=1)

    assert evaluator.evaluate(
        {
            "executor": "llm",
            "id": "story_ending_judge",
            "endpoint": "semantic.story_ending_judge",
            "fallback": True,
            "input": {"round": {"ref": "GAME.round"}},
        },
        state,
        actor=None,
    ) is True


def test_state_actor_can_compare_to_state_value():
    state = _make_state_with_players({"P1": {"alive": False}})
    writer = StateWriter(state)
    writer.apply(SetAttr("GAME", "last_vote_target", "P1"))
    cond = {"state": "actor", "equals_state": "GAME.last_vote_target"}
    assert evaluator.evaluate(cond, state, actor="P1") is True
    assert evaluator.evaluate(cond, state, actor="P2") is False

def test_item_available_actor_keyword():
    players = {"Player_3": {"alive": True, "inventory_poison_potion": 1}}
    state = _make_state_with_players(players)
    assert evaluator.evaluate({"item_available": {"entity": "actor", "item": "poison_potion"}}, state, actor="Player_3") is True

def test_item_available_candidate_keyword():
    players = {"Player_4": {"alive": True, "inventory_token": 1}}
    state = _make_state_with_players(players)
    assert evaluator.evaluate(
        {"item_available": {"entity": "candidate", "item": "token"}},
        state,
        actor="Player_1",
        candidate="Player_4",
    ) is True

def test_not_equals_state():
    state = _make_state(accused="Player_1", real_killer="Player_3")
    assert evaluator.evaluate({"state": "GAME.accused", "not_equals_state": "GAME.real_killer"}, state, actor=None) is True

def test_python_expr_condition():
    state = _make_state(round=1)
    assert evaluator.evaluate(
        {"executor": "code", "language": "python", "code": "result = attr('GAME', 'round') == 1"},
        state,
        actor=None,
    ) is True


def test_python_code_condition():
    players = {
        "P1": {"alive": True, "faction": "wolf"},
        "P2": {"alive": True, "faction": "good"},
    }
    state = _make_state_with_players(players)
    assert evaluator.evaluate(
        {
            "executor": "code",
            "language": "python",
            "code": "result = count({'alive': True, 'faction': 'wolf'}) == 1",
        },
        state,
        actor=None,
    ) is True


