"""端到端测试：狼人杀 v1 YAML → Script 关键规则。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from drama_engine.core.dsl.compiler import YamlCompiler
from drama_engine.core.engine import State, StateWriter, Vocabulary


GUARD_YAML_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "drama_engine",
    "core",
    "scripts",
    "werewolf_v1_guard.yaml",
)

V1_YAML_PATHS = (GUARD_YAML_PATH,)

_EMPTY_VOCAB = Vocabulary(
    roles=frozenset(),
    factions=frozenset(),
    scopes=frozenset(),
    abilities=frozenset(),
)

compiler = YamlCompiler()


def test_werewolf_v1_scripts_use_base_round_condition():
    """狼人杀 v1 脚本应使用基础轮次条件，不再依赖 is_first_round 语义糖。"""
    for yaml_path in V1_YAML_PATHS:
        with open(yaml_path, "r", encoding="utf-8") as file:
            content = file.read()

        assert "is_first_round" not in content
        assert "ref: GAME.round" in content
        assert "less_than_equal: 1" in content


def test_werewolf_v1_sheriff_join_uses_confirm_without_reason():
    """上警只应收集是否确认上警，不要求目标或理由。"""
    for yaml_path in V1_YAML_PATHS:
        script = compiler.compile(yaml_path)
        sheriff_join = next(scene for scene in script.flow.scenes if scene.name == "sheriff-join")

        assert sheriff_join.response_model is not None
        assert set(sheriff_join.response_model.model_fields) == {"action"}


def test_werewolf_v1_seer_check_broadcast_only_reveals_faction():
    """v1 各板子中，预言家查验结果只应公布阵营，不能泄露具体身份。"""
    for yaml_path in V1_YAML_PATHS:
        with open(yaml_path, "r", encoding="utf-8") as file:
            content = file.read()

        assert "查验结果：{data.target} 的阵营是 {data.target.faction}。" in content
        assert "查验结果：{data.target} 的身份是" not in content
        assert "{data.target.role" not in content


def test_validate_werewolf_v1_guard_yaml_no_errors():
    """狼人杀 v1 守卫板 YAML 应通过结构校验。"""
    errors = compiler.validate_file(GUARD_YAML_PATH, {})
    assert errors == []


def test_werewolf_v1_guard_roles_and_distribution():
    """守卫板应是 12 人：4狼4民1预1女1猎1守卫。"""
    script = compiler.compile(GUARD_YAML_PATH)
    role_names = {role.name for role in script.roles}
    assert role_names == {
        "werewolf",
        "villager",
        "seer",
        "witch",
        "hunter",
        "guard",
    }
    assert script.casting.role_counts == {
        "werewolf": 4,
        "villager": 4,
        "seer": 1,
        "witch": 1,
        "hunter": 1,
        "guard": 1,
    }


def test_werewolf_v1_guard_flow_matches_guard_script_order():
    """守卫板夜晚和白天关键流程应符合守卫版规则。"""
    script = compiler.compile(GUARD_YAML_PATH)
    scene_names = [scene.name for scene in script.flow.scenes]

    assert scene_names.index("guard-protect") < scene_names.index("seer-check")
    assert scene_names.index("seer-check") < scene_names.index("wolf-discuss")
    assert scene_names.index("wolf-discuss") < scene_names.index("wolf-vote")
    assert scene_names.index("wolf-vote") < scene_names.index("witch-save")
    assert scene_names.index("witch-save") < scene_names.index("witch-poison")
    assert scene_names.index("witch-poison") < scene_names.index("dawn-resolve")
    assert scene_names.index("dawn-resolve") < scene_names.index("sheriff-election-reset")
    assert scene_names.index("sheriff-election-reset") < scene_names.index("sheriff-join")
    assert scene_names.index("sheriff-pk-vote") < scene_names.index("death-report")

    assert scene_names.index("sheriff-set-speech-order") < scene_names.index("day-discuss-sheriff-order")
    assert scene_names.index("day-discuss-sheriff-order") < scene_names.index("sheriff-lead-speech")
    assert scene_names.index("sheriff-lead-speech") < scene_names.index("day-vote")


def test_guard_cannot_protect_same_target_twice():
    """守卫不能连续两晚守护同一名玩家。"""
    script = compiler.compile(GUARD_YAML_PATH)
    guard_protect = next(scene for scene in script.flow.scenes if scene.name == "guard-protect")

    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {"last_guarded_target": "P1"})
    state.register_entity("Guard", {"alive": True, "role": "guard"})
    state.register_entity("P1", {"alive": True, "role": "villager"})
    state.register_entity("P2", {"alive": True, "role": "villager"})

    assert guard_protect.candidates(state, "Guard") == ["Guard", "P2"]


def test_guard_protects_from_wolf_kill():
    """守卫单独守中狼人目标时，目标不死亡。"""
    script = compiler.compile(GUARD_YAML_PATH)
    dawn_resolve = next(scene for scene in script.flow.scenes if scene.name == "dawn-resolve")

    state = State(_EMPTY_VOCAB)
    state.register_entity(
        "GAME",
        {
            "round": 1,
            "wolf_target": "P1",
            "guarded_target": "P1",
            "saved": False,
            "poison_target": None,
        },
    )
    state.register_entity("P1", {"alive": True, "role": "villager"})
    writer = StateWriter(state)

    dawn_resolve.on_result([], state, writer)

    assert state.get_attr("P1", "alive") is True


def test_guard_and_witch_heal_conflict_kills_target():
    """守卫和女巫解药同时作用于狼人目标时，应发生守药冲突并死亡。"""
    script = compiler.compile(GUARD_YAML_PATH)
    dawn_resolve = next(scene for scene in script.flow.scenes if scene.name == "dawn-resolve")

    state = State(_EMPTY_VOCAB)
    state.register_entity(
        "GAME",
        {
            "round": 1,
            "wolf_target": "P1",
            "guarded_target": "P1",
            "saved": True,
            "poison_target": None,
        },
    )
    state.register_entity("P1", {"alive": True, "role": "villager"})
    writer = StateWriter(state)

    dawn_resolve.on_result([], state, writer)

    assert state.get_attr("P1", "alive") is False
    assert state.get_attr("P1", "death_cause") == "wolf"


def test_guard_death_report_uses_recorded_night_deaths_only():
    """夜晚结果公布应读 GAME.night_deaths，不混入猎枪等连锁死亡。"""
    script = compiler.compile(GUARD_YAML_PATH)
    report = next(scene for scene in script.flow.scenes if scene.name == "death-report")

    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {"round": 2, "night_deaths": []})
    state.register_entity(
        "P1",
        {"alive": False, "death_round": 2, "death_cause": "wolf", "seat_index": 1},
    )
    state.register_entity(
        "P2",
        {"alive": False, "death_round": 2, "death_cause": "shot", "seat_index": 2},
    )
    state.register_entity("P3", {"alive": True, "seat_index": 3})
    writer = StateWriter(state)

    report.on_result([], state, writer)

    assert state.get_attr("GAME", "night_deaths") == ["P1"]
    assert report.publication["messages"][0]["audience"] == "town"
    assert report.publication["disclosures"] == [
        {
            "timing": "after_messages",
            "audience": "town",
            "targets": {"ref": "GAME.night_deaths"},
            "fields": ["alive", "death_round", "death_cause"],
        }
    ]
    assert report.publication["messages"][0]["text"](state) == (
        "天亮了，昨天晚上被淘汰的玩家是P1，"
        "当前存活的玩家P3，游戏继续。"
    )


def test_guard_dawn_resolution_uses_scene_transaction_sections():
    """守卫板黎明结算应使用 gate/interaction/resolution 四段事务写法。"""
    with open(GUARD_YAML_PATH, "r", encoding="utf-8") as file:
        content = file.read()

    assert "resolution:" in content
    assert "publication:" in content
    assert "target:\n          ref: GAME.wolf_target" in content

    script = compiler.compile(GUARD_YAML_PATH)
    dawn_resolve = next(scene for scene in script.flow.scenes if scene.name == "dawn-resolve")
    death_report = next(scene for scene in script.flow.scenes if scene.name == "death-report")

    assert dawn_resolve.on_result is not None
    assert death_report.on_result is not None
    assert death_report.publication["messages"][0]["audience"] == "town"


def test_witch_poison_skips_after_heal_same_night():
    """v1 女巫同一晚用过解药后，不能再进入毒药幕。"""
    script = compiler.compile(GUARD_YAML_PATH)
    witch_poison = next(scene for scene in script.flow.scenes if scene.name == "witch-poison")

    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {"saved": True})
    state.register_entity(
        "Player_1",
        {
            "alive": True,
            "role": "witch",
            "inventory_poison_potion": 1,
        },
    )

    assert witch_poison.participants(state) == set()


def test_witch_cannot_save_self():
    """v1 女巫被狼人刀中时不能使用解药救自己。"""
    script = compiler.compile(GUARD_YAML_PATH)
    witch_save = next(scene for scene in script.flow.scenes if scene.name == "witch-save")

    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {"wolf_target": "Player_1"})
    state.register_entity(
        "Player_1",
        {
            "alive": True,
            "role": "witch",
            "inventory_heal_potion": 1,
        },
    )

    assert witch_save.participants(state) == set()


def test_wolf_can_choose_no_kill():
    """狼人夜晚候选应包含 NO_KILL，表示空刀。"""
    script = compiler.compile(GUARD_YAML_PATH)
    wolf_vote = next(scene for scene in script.flow.scenes if scene.name == "wolf-vote")

    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {})
    state.register_entity("Wolf", {"alive": True, "role": "werewolf"})
    state.register_entity("Villager", {"alive": True, "role": "villager"})

    assert "NO_KILL" in wolf_vote.candidates(state, "Wolf")


def test_no_kill_does_not_set_wolf_target():
    """狼人选择 NO_KILL 时不记录 wolf_target。"""
    script = compiler.compile(GUARD_YAML_PATH)
    wolf_vote = next(scene for scene in script.flow.scenes if scene.name == "wolf-vote")

    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {})
    writer = StateWriter(state)
    responses = [
        {"actor": "Wolf", "data": {"vote": "NO_KILL", "reason": "test"}},
    ]

    wolf_vote.on_result(responses, state, writer)

    assert state.get_attr("GAME", "wolf_target") is None


def test_idiot_reveals_instead_of_dying_when_voted_out():
    """白痴被放逐时应翻牌留场，而不是死亡。"""
    script = compiler.compile(GUARD_YAML_PATH)
    day_vote = next(scene for scene in script.flow.scenes if scene.name == "day-vote")

    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {"round": 1})
    state.register_entity("Player_1", {"alive": True, "role": "idiot", "revealed_idiot": False})
    state.register_entity("Player_2", {"alive": True, "role": "villager", "revealed_idiot": False})
    writer = StateWriter(state)
    responses = [
        {"actor": "Player_2", "data": {"vote": "Player_1", "reason": "test"}},
    ]

    day_vote.on_result(responses, state, writer)

    assert state.get_attr("Player_1", "alive") is True
    assert state.get_attr("Player_1", "revealed_idiot") is True
    assert state.get_attr("GAME", "last_vote_target") == "Player_1"


def test_revealed_idiot_cannot_vote_or_be_voted_again():
    """翻牌白痴后续可发言，但不能投票，也不能成为放逐候选。"""
    script = compiler.compile(GUARD_YAML_PATH)
    day_vote = next(scene for scene in script.flow.scenes if scene.name == "day-vote")

    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {"round": 2})
    state.register_entity("Player_1", {"alive": True, "role": "idiot", "revealed_idiot": True})
    state.register_entity("Player_2", {"alive": True, "role": "villager", "revealed_idiot": False})

    assert day_vote.participants(state) == {"Player_2"}
    assert day_vote.candidates(state, "Player_2") == ["Player_2"]


def test_day_vote_uses_sheriff_weight():
    """警长 1.5 票应影响放逐投票结果。"""
    script = compiler.compile(GUARD_YAML_PATH)
    day_vote = next(scene for scene in script.flow.scenes if scene.name == "day-vote")

    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {"round": 2})
    state.register_entity("Sheriff", {"alive": True, "role": "villager", "vote_weight": 1.5})
    state.register_entity("P2", {"alive": True, "role": "villager", "vote_weight": 1})
    state.register_entity("P3", {"alive": True, "role": "villager", "vote_weight": 1})
    writer = StateWriter(state)
    responses = [
        {"actor": "Sheriff", "data": {"vote": "P2", "reason": "sheriff"}},
        {"actor": "P2", "data": {"vote": "P3", "reason": "p2"}},
        {"actor": "P3", "data": {"vote": "P3", "reason": "p3"}},
    ]

    day_vote.on_result(responses, state, writer)

    assert state.get_attr("P3", "alive") is False
    assert state.get_attr("GAME", "last_vote_target") == "P3"


def test_sheriff_direction_builds_day_speech_order():
    """警长指定发言方向后，应按参考点和座位生成 GAME.day_speech_order。"""
    script = compiler.compile(GUARD_YAML_PATH)
    set_order = next(scene for scene in script.flow.scenes if scene.name == "sheriff-set-speech-order")

    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {"has_sheriff": True, "sheriff": "P2", "night_deaths": []})
    state.register_entity("P1", {"alive": True, "role": "villager", "seat_index": 1})
    state.register_entity("P2", {"alive": True, "role": "villager", "seat_index": 2})
    state.register_entity("P3", {"alive": True, "role": "villager", "seat_index": 3})
    writer = StateWriter(state)
    responses = [
        {"actor": "P2", "data": {"target": "left", "reason": "test"}},
    ]

    set_order.on_result(responses, state, writer)

    assert state.get_attr("GAME", "day_speech_order") == ["P3", "P1", "P2"]


def test_guard_sheriff_speech_direction_uses_left_right_candidates():
    """守卫板警长指定发言方向时，只能选择 left/right。"""
    script = compiler.compile(GUARD_YAML_PATH)
    set_order = next(scene for scene in script.flow.scenes if scene.name == "sheriff-set-speech-order")

    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {"has_sheriff": True, "sheriff": "Sheriff"})
    state.register_entity("Sheriff", {"alive": True, "role": "villager"})
    state.register_entity("P1", {"alive": True, "role": "villager"})
    state.register_entity("P2", {"alive": True, "role": "villager"})
    state.register_entity("Dead", {"alive": False, "role": "villager"})

    assert set_order.candidates(state, "Sheriff") == ["left", "right"]


def test_day_discussion_uses_sheriff_ordered_participants():
    """有警长发言顺序时，白天讨论应按 GAME.day_speech_order 返回演员。"""
    script = compiler.compile(GUARD_YAML_PATH)
    ordered_discuss = next(scene for scene in script.flow.scenes if scene.name == "day-discuss-sheriff-order")

    state = State(_EMPTY_VOCAB)
    state.register_entity(
        "GAME",
        {
            "has_sheriff": True,
            "day_speech_order": ["P2", "Sheriff", "P1"],
        },
    )
    state.register_entity("Sheriff", {"alive": True, "role": "villager"})
    state.register_entity("P1", {"alive": True, "role": "villager"})
    state.register_entity("P2", {"alive": True, "role": "villager"})

    assert ordered_discuss.participants(state) == ["P2", "Sheriff", "P1"]


def test_day_vote_tie_enters_pk_without_killing():
    """首次放逐平票时应进入 PK，不立即出局。"""
    script = compiler.compile(GUARD_YAML_PATH)
    day_vote = next(scene for scene in script.flow.scenes if scene.name == "day-vote")

    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {"round": 2, "day_pk_candidates": [], "need_day_pk": False})
    state.register_entity("P1", {"alive": True, "role": "villager", "vote_weight": 1})
    state.register_entity("P2", {"alive": True, "role": "villager", "vote_weight": 1})
    writer = StateWriter(state)
    responses = [
        {"actor": "P1", "data": {"vote": "P2", "reason": "a"}},
        {"actor": "P2", "data": {"vote": "P1", "reason": "b"}},
    ]

    day_vote.on_result(responses, state, writer)

    assert state.get_attr("P1", "alive") is True
    assert state.get_attr("P2", "alive") is True
    assert state.get_attr("GAME", "need_day_pk") is True
    assert state.get_attr("GAME", "day_pk_candidates") == ["P1", "P2"]


def test_day_pk_second_tie_exiles_nobody():
    """PK 二次投票再次平票时，本轮无人出局。"""
    script = compiler.compile(GUARD_YAML_PATH)
    day_pk_vote = next(scene for scene in script.flow.scenes if scene.name == "day-pk-vote")

    state = State(_EMPTY_VOCAB)
    state.register_entity(
        "GAME",
        {
            "round": 2,
            "need_day_pk": True,
            "day_pk_candidates": ["P1", "P2"],
            "last_vote_target": "old",
        },
    )
    state.register_entity("P1", {"alive": True, "role": "villager", "vote_weight": 1})
    state.register_entity("P2", {"alive": True, "role": "villager", "vote_weight": 1})
    state.register_entity("P3", {"alive": True, "role": "villager", "vote_weight": 1})
    state.register_entity("P4", {"alive": True, "role": "villager", "vote_weight": 1})
    writer = StateWriter(state)
    responses = [
        {"actor": "P3", "data": {"vote": "P1", "reason": "a"}},
        {"actor": "P4", "data": {"vote": "P2", "reason": "b"}},
    ]

    day_pk_vote.on_result(responses, state, writer)

    assert state.get_attr("P1", "alive") is True
    assert state.get_attr("P2", "alive") is True
    assert state.get_attr("GAME", "last_vote_target") is None
    assert state.get_attr("GAME", "need_day_pk") is False
    assert state.get_attr("GAME", "day_pk_candidates") == []


def test_sheriff_join_and_withdraw_use_all_responses():
    """上警和退水应逐个处理所有 response。"""
    script = compiler.compile(GUARD_YAML_PATH)
    sheriff_join = next(scene for scene in script.flow.scenes if scene.name == "sheriff-join")
    sheriff_withdraw = next(scene for scene in script.flow.scenes if scene.name == "sheriff-withdraw")

    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {"sheriff_candidates": []})
    writer = StateWriter(state)
    join_responses = [
        {"actor": "P1", "data": {"action": True}},
        {"actor": "P2", "data": {"action": False}},
        {"actor": "P3", "data": {"action": True}},
    ]
    sheriff_join.on_result(join_responses, state, writer)
    assert state.get_attr("GAME", "sheriff_candidates") == ["P1", "P3"]

    withdraw_responses = [
        {"actor": "P1", "data": {"action": True, "reason": "withdraw"}},
        {"actor": "P3", "data": {"action": False, "reason": "stay"}},
    ]
    sheriff_withdraw.on_result(withdraw_responses, state, writer)
    assert state.get_attr("GAME", "sheriff_candidates") == ["P3"]


def test_sheriff_vote_sets_weight_and_pk_on_tie():
    """警长投票应能设置 1.5 票；平票时进入警长 PK。"""
    script = compiler.compile(GUARD_YAML_PATH)
    sheriff_vote = next(scene for scene in script.flow.scenes if scene.name == "sheriff-vote")

    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {"sheriff_candidates": ["P1", "P2"], "sheriff_pk_candidates": []})
    state.register_entity("P1", {"alive": True, "role": "villager", "vote_weight": 1})
    state.register_entity("P2", {"alive": True, "role": "villager", "vote_weight": 1})
    state.register_entity("P3", {"alive": True, "role": "villager", "vote_weight": 1})
    state.register_entity("P4", {"alive": True, "role": "villager", "vote_weight": 1})
    writer = StateWriter(state)
    responses = [
        {"actor": "P3", "data": {"vote": "P1", "reason": "a"}},
        {"actor": "P4", "data": {"vote": "P1", "reason": "b"}},
    ]
    sheriff_vote.on_result(responses, state, writer)
    assert state.get_attr("GAME", "sheriff") == "P1"
    assert state.get_attr("P1", "vote_weight") == 1.5

    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {"sheriff_candidates": ["P1", "P2"], "sheriff_pk_candidates": []})
    state.register_entity("P1", {"alive": True, "role": "villager", "vote_weight": 1})
    state.register_entity("P2", {"alive": True, "role": "villager", "vote_weight": 1})
    writer = StateWriter(state)
    responses = [
        {"actor": "P3", "data": {"vote": "P1", "reason": "a"}},
        {"actor": "P4", "data": {"vote": "P2", "reason": "b"}},
    ]
    sheriff_vote.on_result(responses, state, writer)
    assert state.get_attr("GAME", "need_sheriff_pk") is True
    assert state.get_attr("GAME", "sheriff_pk_candidates") == ["P1", "P2"]


def test_sheriff_pk_only_runs_on_first_day():
    """警长竞选 PK 只应在第一天触发，避免后续天数误进入警长竞选。"""
    script = compiler.compile(GUARD_YAML_PATH)
    sheriff_pk_speech = next(scene for scene in script.flow.scenes if scene.name == "sheriff-pk-speech")
    sheriff_pk_vote = next(scene for scene in script.flow.scenes if scene.name == "sheriff-pk-vote")

    state = State(_EMPTY_VOCAB)
    state.register_entity(
        "GAME",
        {
            "round": 2,
            "need_sheriff_pk": True,
            "sheriff_pk_candidates": ["P1", "P2"],
        },
    )

    assert sheriff_pk_speech.when(state) is False
    assert sheriff_pk_vote.when(state) is False


def test_sheriff_badge_transfer_gives_new_sheriff_weight():
    """警长出局后移交警徽时，新警长应获得 1.5 票权。"""
    script = compiler.compile(GUARD_YAML_PATH)
    transfer = next(scene for scene in script.flow.scenes if scene.name == "sheriff-badge-transfer-day")

    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {"round": 2, "has_sheriff": True, "sheriff": "P1"})
    state.register_entity("P1", {"alive": False, "role": "villager", "vote_weight": 1.5, "death_round": 2})
    state.register_entity("P2", {"alive": True, "role": "villager", "vote_weight": 1})
    writer = StateWriter(state)
    responses = [
        {"actor": "P1", "data": {"action": True, "target": "P2", "reason": "trust"}},
    ]

    transfer.on_result(responses, state, writer)

    assert state.get_attr("GAME", "sheriff") == "P2"
    assert state.get_attr("GAME", "has_sheriff") is True
    assert state.get_attr("P1", "vote_weight") == 1
    assert state.get_attr("P2", "vote_weight") == 1.5


def test_sheriff_badge_destroy_removes_sheriff():
    """警长出局后撕毁警徽时，本局应不再有警长。"""
    script = compiler.compile(GUARD_YAML_PATH)
    transfer = next(scene for scene in script.flow.scenes if scene.name == "sheriff-badge-transfer-day")

    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {"round": 2, "has_sheriff": True, "sheriff": "P1"})
    state.register_entity("P1", {"alive": False, "role": "villager", "vote_weight": 1.5, "death_round": 2})
    state.register_entity("P2", {"alive": True, "role": "villager", "vote_weight": 1})
    writer = StateWriter(state)
    responses = [
        {"actor": "P1", "data": {"action": False, "target": None, "reason": "destroy"}},
    ]

    transfer.on_result(responses, state, writer)

    assert state.get_attr("GAME", "sheriff") is None
    assert state.get_attr("GAME", "has_sheriff") is False
    assert state.get_attr("P1", "vote_weight") == 1
    assert state.get_attr("P2", "vote_weight") == 1


def test_wolves_win_when_all_villagers_are_dead():
    """刀边：平民全部出局时狼人胜利。"""
    script = compiler.compile(GUARD_YAML_PATH)
    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {"round": 3})
    state.register_entity("Wolf", {"alive": True, "role": "werewolf", "faction": "wolf"})
    state.register_entity("Seer", {"alive": True, "role": "seer", "faction": "good"})
    state.register_entity("Villager", {"alive": False, "role": "villager", "faction": "good"})

    result = script.referee(state)

    assert result is not None
    assert "狼人" in result


def test_wolves_win_when_all_gods_are_dead():
    """刀边：神职全部出局时狼人胜利。"""
    script = compiler.compile(GUARD_YAML_PATH)
    state = State(_EMPTY_VOCAB)
    state.register_entity("GAME", {"round": 3})
    state.register_entity("Wolf", {"alive": True, "role": "werewolf", "faction": "wolf"})
    state.register_entity("Villager", {"alive": True, "role": "villager", "faction": "good"})
    state.register_entity("Seer", {"alive": False, "role": "seer", "faction": "good"})
    state.register_entity("Witch", {"alive": False, "role": "witch", "faction": "good"})
    state.register_entity("Hunter", {"alive": False, "role": "hunter", "faction": "good"})
    state.register_entity("Idiot", {"alive": False, "role": "idiot", "faction": "good"})

    result = script.referee(state)

    assert result is not None
    assert "狼人" in result
