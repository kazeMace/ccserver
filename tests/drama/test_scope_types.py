"""扩展 Scope 成员函数测试。"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from drama_engine.core.engine import State, StateWriter, SetAttr, Vocabulary
from drama_engine.core.dsl.components.scope_types import make_self_scope_members, make_dynamic_whisper_members

# 测试用最小词汇表，不做任何词汇校验（空集合）
_EMPTY_VOCAB = Vocabulary(
    roles=frozenset(),
    factions=frozenset(),
    scopes=frozenset(),
    abilities=frozenset(),
)


def _make_state(players):
    """创建 state 并注册给定的玩家。

    参数：
      players — dict，玩家名 -> 属性字典，如 {"P1": {"alive": True}, "P2": {"alive": True}}
    """
    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {})
    for name, attrs in players.items():
        state.register_entity(name, attrs)
    return state


def test_self_scope_returns_only_actor():
    """测试 self scope 只返回当前 actor（忏悔室场景）。"""
    state = _make_state({"P1": {"alive": True}, "P2": {"alive": True}})
    members_fn = make_self_scope_members()

    # 设置当前 actor 为 P1
    w = StateWriter(state)
    w.apply(SetAttr("GAME", "__current_actor", "P1"))

    # self scope 的成员应该只有 P1
    result = members_fn(state)
    assert result == {"P1"}


def test_self_scope_empty_when_no_actor():
    """测试当没有 __current_actor 时，self scope 返回空集。"""
    state = _make_state({"P1": {"alive": True}})
    members_fn = make_self_scope_members()

    # 没有设置 __current_actor
    result = members_fn(state)
    assert result == set()


def test_self_scope_different_actors():
    """测试 self scope 随 __current_actor 变化。"""
    state = _make_state({"P1": {"alive": True}, "P2": {"alive": True}})
    members_fn = make_self_scope_members()

    w = StateWriter(state)

    # 先设为 P1
    w.apply(SetAttr("GAME", "__current_actor", "P1"))
    assert members_fn(state) == {"P1"}

    # 改为 P2
    w.apply(SetAttr("GAME", "__current_actor", "P2"))
    assert members_fn(state) == {"P2"}


def test_dynamic_whisper_returns_pair():
    """测试 dynamic_whisper scope 返回 actor + target 双方。"""
    state = _make_state({"P1": {"alive": True}, "P2": {"alive": True}})
    members_fn = make_dynamic_whisper_members()

    w = StateWriter(state)
    w.apply(SetAttr("GAME", "__current_actor", "P1"))
    w.apply(SetAttr("GAME", "__dynamic_whisper_target", "P2"))

    # dynamic_whisper scope 应该包含 P1 和 P2
    result = members_fn(state)
    assert result == {"P1", "P2"}


def test_dynamic_whisper_single_when_no_target():
    """测试 dynamic_whisper scope 在没有 target 时只返回 actor。"""
    state = _make_state({"P1": {"alive": True}})
    members_fn = make_dynamic_whisper_members()

    w = StateWriter(state)
    w.apply(SetAttr("GAME", "__current_actor", "P1"))
    # 不设置 __dynamic_whisper_target

    # 应该只返回 P1
    result = members_fn(state)
    assert result == {"P1"}


def test_dynamic_whisper_empty_when_no_actor():
    """测试 dynamic_whisper scope 在没有 actor 时返回空集。"""
    state = _make_state({"P1": {"alive": True}})
    members_fn = make_dynamic_whisper_members()

    # 不设置 __current_actor 和 __dynamic_whisper_target
    result = members_fn(state)
    assert result == set()


def test_dynamic_whisper_with_only_target():
    """测试 dynamic_whisper scope 在只有 target 没有 actor 时只返回 target。"""
    state = _make_state({"P1": {"alive": True}, "P2": {"alive": True}})
    members_fn = make_dynamic_whisper_members()

    w = StateWriter(state)
    # 只设置 target，不设 actor
    w.apply(SetAttr("GAME", "__dynamic_whisper_target", "P2"))

    result = members_fn(state)
    # 应该只返回 P2
    assert result == {"P2"}


def test_dynamic_whisper_actor_equals_target():
    """测试 dynamic_whisper scope 当 actor 和 target 相同时返回单个元素。"""
    state = _make_state({"P1": {"alive": True}})
    members_fn = make_dynamic_whisper_members()

    w = StateWriter(state)
    w.apply(SetAttr("GAME", "__current_actor", "P1"))
    w.apply(SetAttr("GAME", "__dynamic_whisper_target", "P1"))

    # 即使 actor 和 target 相同，集合中也只有一个元素
    result = members_fn(state)
    assert result == {"P1"}
