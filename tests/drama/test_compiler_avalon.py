"""阿瓦隆 YAML 剧本与专用规则插件测试。"""

from pathlib import Path

from pydantic import create_model

from drama_engine.core.dsl.compiler import YamlCompiler
from drama_engine.core.engine import Scene, SetAttr, Single, State, StateWriter
from drama_engine.core.dsl.plugins import EffectContext, build_default_plugin_registry
from drama_engine.core.engine import Vocabulary
from drama_engine.core.engine import _candidate_constraint_error


AVALON_YAML = Path("drama_engine/core/scripts/avalon.yaml")


def _state() -> State:
    """构造含 GAME 与 5 名玩家的最小阿瓦隆测试状态。"""
    state = State(Vocabulary(roles={"merlin", "assassin"}, factions={"good", "evil"}, scopes={"public"}, abilities=set()))
    state.register_entity("GAME", {})
    for index in range(1, 6):
        state.register_entity(f"Player_{index}", {"seat_index": index, "in_game": True})
    return state


def test_avalon_yaml_validates_and_compiles() -> None:
    """avalon.yaml 应通过现有 YAML 编译链路。"""
    compiler = YamlCompiler()

    errors = compiler.validate_file(str(AVALON_YAML), {})
    script = compiler.compile(str(AVALON_YAML), {})

    assert errors == []
    assert script.player_config.count == 5
    assert {role.name for role in script.roles} == {
        "merlin",
        "percival",
        "loyal_servant",
        "morgana",
        "assassin",
    }
    assert script.extensions["avalon"]["rules"]["quest_team_sizes"] == [2, 3, 2, 3, 3]


def test_dynamic_choose_many_count_reads_state() -> None:
    """ChooseMany 数量约束应支持从 GAME.current_team_size 读取。"""
    model = create_model("ChooseManyModel", targets=(list[str], ...))
    scene = Scene(
        name="leader-nominate-team",
        scope="public",
        participants=lambda state: {"Player_1"},
        cue="choose",
        dialogue_policy=Single(),
        response_model=model,
        candidate_constraints={"count": {"state": "GAME.current_team_size"}, "distinct": True},
    )
    state = _state()
    StateWriter(state).apply(SetAttr("GAME", "current_team_size", 3))

    error = _candidate_constraint_error(
        scene,
        selected=["Player_1", "Player_2"],
        candidates=["Player_1", "Player_2", "Player_3", "Player_4", "Player_5"],
        state=state,
    )

    assert "必须选择 3 个目标" in error


def test_avalon_plugin_resolves_mission_and_routes_to_assassination() -> None:
    """好人拿到第三次任务成功后，应进入刺杀梅林阶段。"""
    registry = build_default_plugin_registry()
    state = _state()
    writer = StateWriter(state)
    writer.apply(SetAttr("GAME", "good_score", 2))
    writer.apply(SetAttr("GAME", "evil_score", 0))
    writer.apply(SetAttr("GAME", "quest_number", 3))
    writer.apply(SetAttr("GAME", "leader_index", 1))
    writer.apply(SetAttr("GAME", "player_count", 5))
    writer.apply(SetAttr("GAME", "current_fail_threshold", 1))
    writer.apply(SetAttr("GAME", "mission_fail_count", 0))

    registry.execute_effect(
        {
            "type": "avalon_resolve_mission",
            "ifs": [
                {"when": {"value": {"ref": "GAME.good_score"}, "greater_than_equal": 2}, "state": "assassination"},
            ],
            "default": "team_building",
        },
        EffectContext(
            state=state,
            writer=writer,
            actor=None,
            responses=[],
            scene_name="mission-card",
            extra={"script_extensions": {"avalon": {"rules": {"quest_team_sizes": [2, 3, 2, 3, 3], "quest_fail_thresholds": [1, 1, 1, 1, 1]}}}},
        ),
    )

    assert state.get_attr("GAME", "good_score") == 3
    assert state.get_attr("GAME", "__flow_next_state") == "assassination"
