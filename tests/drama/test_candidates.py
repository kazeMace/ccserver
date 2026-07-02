import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from drama_engine.core.engine import State, StateWriter, SetAttr, Vocabulary
from drama_engine.core.dsl.components.candidates import CandidateResolver
from drama_engine.core.dsl.components.conditions import ConditionEvaluator

_EMPTY_VOCAB = Vocabulary(roles=frozenset(), factions=frozenset(), scopes=frozenset(), abilities=frozenset())
evaluator = ConditionEvaluator()
resolver = CandidateResolver(evaluator)

def _make_state_with_players(players):
    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {})
    for name, attrs in players.items():
        state.register_entity(name, attrs)
    return state

def test_filter_candidates():
    players = {
        "P1": {"alive": True, "faction": "good"},
        "P2": {"alive": True, "faction": "wolf"},
        "P3": {"alive": False, "faction": "good"},
    }
    state = _make_state_with_players(players)
    result = resolver.resolve({"filter": {"alive": True, "faction": "good"}}, state, last_responses=[])
    assert result == ["P1"]

def test_filter_candidates_supports_value_condition():
    players = {
        "P1": {"alive": True, "faction": "good"},
        "P2": {"alive": True, "faction": "wolf"},
        "P3": {"alive": False, "faction": "good"},
    }
    state = _make_state_with_players(players)
    result = resolver.resolve(
        {
            "filter": {
                "all": [
                    {"value": "alive", "equal": True},
                    {"value": "faction", "equal": "good"},
                ]
            }
        },
        state,
        last_responses=[],
    )
    assert result == ["P1"]

def test_static_candidates():
    state = _make_state_with_players({})
    result = resolver.resolve({"static": ["选项A", "选项B", "选项C"]}, state, last_responses=[])
    assert result == ["选项A", "选项B", "选项C"]

def test_from_data_candidates():
    state = _make_state_with_players({})
    last_responses = [{"actor": "P1", "data": {"target": "Player_3"}}]
    result = resolver.resolve({"from_data": "target"}, state, last_responses=last_responses)
    assert result == ["Player_3"]

def test_all_players_when_no_spec():
    players = {"P1": {"alive": True}, "P2": {"alive": False}, "P3": {"alive": True}}
    state = _make_state_with_players(players)
    result = resolver.resolve({}, state, last_responses=[])
    assert set(result) == {"P1", "P2", "P3"}

def test_candidates_when_can_exclude_actor():
    players = {
        "P1": {"alive": True},
        "P2": {"alive": True},
        "P3": {"alive": False},
    }
    state = _make_state_with_players(players)
    result = resolver.resolve(
        {
            "filter": {"alive": True},
            "when": {"state": "candidate", "not_equals_state": "actor"},
        },
        state,
        last_responses=[],
        actor="P1",
    )
    assert result == ["P2"]

def test_candidates_when_supports_value_ref():
    players = {
        "P1": {"alive": True},
        "P2": {"alive": True},
        "P3": {"alive": False},
    }
    state = _make_state_with_players(players)
    result = resolver.resolve(
        {
            "filter": {"value": "alive", "equal": True},
            "when": {"value": {"ref": "candidate"}, "not_equal": {"ref": "actor"}},
        },
        state,
        last_responses=[],
        actor="P1",
    )
    assert result == ["P2"]

def test_candidates_when_can_compare_candidate_attrs_to_actor_attrs():
    players = {
        "P1": {"alive": True, "faction": "good"},
        "P2": {"alive": True, "faction": "wolf"},
        "P3": {"alive": True, "faction": "good"},
    }
    state = _make_state_with_players(players)
    result = resolver.resolve(
        {
            "filter": {"alive": True},
            "when": {
                "state": "candidate.faction",
                "not_equals_state": "actor.faction",
            },
        },
        state,
        last_responses=[],
        actor="P1",
    )
    assert result == ["P2"]

def test_candidates_when_list_uses_all_conditions():
    players = {
        "P1": {"alive": True, "role": "seer"},
        "P2": {"alive": True, "role": "villager"},
        "P3": {"alive": True, "role": "werewolf"},
    }
    state = _make_state_with_players(players)
    result = resolver.resolve(
        {
            "filter": {"alive": True},
            "when": [
                {"state": "candidate", "not_equals_state": "actor"},
                {"state": "candidate.role", "not_equals": "werewolf"},
            ],
        },
        state,
        last_responses=[],
        actor="P1",
    )
    assert result == ["P2"]
