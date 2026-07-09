"""计分系统（ScoreTracker）测试。"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from drama_engine.core.engine import State, StateWriter, SetAttr, Vocabulary
from drama_engine.core.components.scoring import ScoreTracker

# 测试用最小词汇表，不做任何词汇校验（空集合）
_EMPTY_VOCAB = Vocabulary(
    roles=frozenset(),
    factions=frozenset(),
    scopes=frozenset(),
    abilities=frozenset(),
)


def _make_state():
    """创建最小 state：只有 GAME 实体。"""
    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {})
    return state


def test_init_teams():
    """测试 ScoreTracker.init() 初始化团队分数为 0。"""
    state = _make_state()
    writer = StateWriter(state)

    scoring_spec = {
        "teams": [
            {"name": "wolf_team", "display_name": "狼人阵营", "members": {"filter": {"faction": "wolf"}}},
            {"name": "good_team", "display_name": "好人阵营", "members": {"filter": {"faction": "good"}}},
        ]
    }

    tracker = ScoreTracker(scoring_spec)
    tracker.init(state, writer)

    # 验证两个团队都已初始化，分数为 0
    assert state.get_attr("wolf_team", "score") == 0
    assert state.get_attr("good_team", "score") == 0


def test_add_score():
    """测试 ScoreTracker.add_score() 增加分数。"""
    state = _make_state()
    state.register_entity("good_team", {"score": 10})
    writer = StateWriter(state)

    tracker = ScoreTracker({"teams": []})
    tracker.add_score("good_team", 5, state, writer)

    # 验证分数从 10 增加到 15
    assert state.get_attr("good_team", "score") == 15


def test_get_scores():
    """测试 ScoreTracker.get_scores() 获取所有团队分数。"""
    state = _make_state()
    state.register_entity("wolf_team", {"score": 20})
    state.register_entity("good_team", {"score": 15})

    tracker = ScoreTracker({
        "teams": [
            {"name": "wolf_team"},
            {"name": "good_team"},
        ]
    })

    scores = tracker.get_scores(state)

    # 验证返回的分数字典
    assert scores == {"wolf_team": 20, "good_team": 15}


def test_add_score_from_zero():
    """测试从 0 分开始增加分数。"""
    state = _make_state()
    state.register_entity("team_a", {"score": 0})
    writer = StateWriter(state)

    tracker = ScoreTracker({"teams": []})
    tracker.add_score("team_a", 10, state, writer)

    # 验证分数从 0 增加到 10
    assert state.get_attr("team_a", "score") == 10


def test_get_scores_missing_entity():
    """测试获取不存在的团队分数时返回 0。"""
    state = _make_state()
    state.register_entity("team_a", {"score": 5})

    tracker = ScoreTracker({
        "teams": [
            {"name": "team_a"},
            {"name": "team_b"},  # team_b 不存在
        ]
    })

    scores = tracker.get_scores(state)

    # 验证 team_a 返回正确分数，team_b 返回 0（默认值）
    assert scores["team_a"] == 5
    assert scores["team_b"] == 0
