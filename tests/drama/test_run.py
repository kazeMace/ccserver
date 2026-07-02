# tests/drama/test_run.py
"""run.py 预设加载与参数合并测试。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from drama_engine.run_script import (
    load_preset,
    merge_params,
    parse_cli_params,
    resolve_human_players,
)

PRESET_PATH = os.path.join(
    os.path.dirname(__file__), '..', '..', 'drama_engine', 'core', 'presets', 'werewolf_9p_normal.preset.yaml'
)
DRAMA_ROOT = os.path.join(os.path.dirname(__file__), '..', '..', 'drama_engine')


def test_load_preset_returns_dict():
    preset = load_preset(PRESET_PATH)
    assert preset["name"] == "9人标准局"
    assert preset["params"]["total_players"] == 9
    assert preset["params"]["werewolf_count"] == 3


def test_load_preset_resolves_script_path():
    preset = load_preset(PRESET_PATH)
    script_rel = preset["script"]
    assert script_rel == "core/scripts/werewolf_v1_guard.yaml"


def test_merge_params_cli_overrides_preset():
    preset_params = {"total_players": 9, "werewolf_count": 3}
    cli_params = {"werewolf_count": 2}
    merged = merge_params(preset_params, cli_params)
    assert merged["total_players"] == 9
    assert merged["werewolf_count"] == 2


def test_merge_params_empty_cli():
    preset_params = {"total_players": 9}
    merged = merge_params(preset_params, {})
    assert merged == {"total_players": 9}


def test_parse_cli_params_basic():
    result = parse_cli_params(["total_players=9", "werewolf_count=3"])
    assert result["total_players"] == 9
    assert result["werewolf_count"] == 3


def test_parse_cli_params_string_value():
    result = parse_cli_params(["mode=normal"])
    assert result["mode"] == "normal"


def test_parse_cli_params_list_value():
    result = parse_cli_params(["human_players=Player_1,Player_2"])
    assert result["human_players"] == ["Player_1", "Player_2"]


def test_parse_cli_params_bool():
    result = parse_cli_params(["include_hunter=true"])
    assert result["include_hunter"] is True
    result2 = parse_cli_params(["include_hunter=false"])
    assert result2["include_hunter"] is False


def test_resolve_human_players_empty_keeps_multi_agent_mode():
    assert resolve_human_players({}) == set()
    assert resolve_human_players({"human_players": []}) == set()
    assert bool(resolve_human_players({})) is False


def test_resolve_human_players_accepts_string_or_list():
    assert resolve_human_players({"human_players": "Player_1, Player_2"}) == {
        "Player_1",
        "Player_2",
    }
    assert resolve_human_players({"human_players": ["Player_3", "Player_4"]}) == {
        "Player_3",
        "Player_4",
    }
    assert bool(resolve_human_players({"human_players": ["Player_3"]})) is True
