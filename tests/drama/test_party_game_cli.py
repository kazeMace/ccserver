"""Party Game DSL CLI tests."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import yaml

import pytest

from drama_engine.cli import CliError, package_script, preview_script, run_cli, simulate_script, validate_script


SCRIPT_PATH = Path("drama_engine/core/scripts/werewolf_v1_guard.yaml")
SCRIPT_DIR = Path("drama_engine/core/scripts")
PRESET_DIR = Path("drama_engine/scripts/presets")
PRESET_PATH = Path("drama_engine/scripts/presets/deduction/werewolf/werewolf_v1_12p_guard.preset.yaml")
REQUIRED_NAMED_GAME_SCRIPTS = {
    "werewolf_v1_guard.yaml",
    "avalon.yaml",
    "who_is_undercover.yaml",
    "uno_lite.yaml",
    "exploding_kittens_lite.yaml",
    "texas_holdem_party_lite.yaml",
    "card_event_party_lite.yaml",
    "gomoku_lite.yaml",
    "xiangqi_lite.yaml",
    "go_lite.yaml",
    "checkers_lite.yaml",
    "monopoly_lite.yaml",
    "flight_chess_lite.yaml",
    "dice_map_adventure_lite.yaml",
    "asset_trading_party_lite.yaml",
    "dnd_fixed_adventure.yaml",
    "coc_fixed_mystery.yaml",
    "story_campaign_lite.yaml",
    "text_adventure_lite.yaml",
    "agent_dm_adventure_lite.yaml",
}


def test_validate_preview_and_simulate_guard_script() -> None:
    """CLI helpers should produce usable reports for the main guard script."""
    validation = validate_script(SCRIPT_PATH)
    preview = preview_script(SCRIPT_PATH)
    simulation = simulate_script(SCRIPT_PATH)

    assert validation.passed()
    assert preview["overview"]["scene_count"] > 0
    assert preview["issues"]["passed"] is True
    assert simulation["passed"] is True
    assert simulation["runtime_type"] == "game_session"
    assert simulation["scene_count"] > 0


def test_validate_and_simulate_preset_resolves_underlying_script() -> None:
    """CLI helpers should treat .preset.yaml as a script plus params wrapper."""
    validation = validate_script(PRESET_PATH)
    preview = preview_script(PRESET_PATH)
    simulation = simulate_script(PRESET_PATH)

    assert validation.passed()
    assert preview["issues"]["passed"] is True
    assert preview["preset_path"].endswith("werewolf_v1_12p_guard.preset.yaml")
    assert simulation["passed"] is True
    assert simulation["preset_path"].endswith("werewolf_v1_12p_guard.preset.yaml")
    assert simulation["runtime_type"] == "game_session"


def test_package_script_writes_publish_zip(tmp_path: Path) -> None:
    """package command should include DSL, validation, simulation and preview files."""
    output = tmp_path / "werewolf_package.zip"

    report = package_script(SCRIPT_PATH, output)

    assert report["passed"] is True
    assert output.exists()
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert SCRIPT_PATH.name in names
        assert "manifest.json" in names
        assert "validation_report.json" in names
        assert "simulation_report.json" in names
        assert "preview.json" in names
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    assert manifest["validation_passed"] is True
    assert manifest["simulation_passed"] is True


def test_package_script_includes_preset_wrapper(tmp_path: Path) -> None:
    """package command should preserve preset entrypoints and merged params."""
    output = tmp_path / "werewolf_preset_package.zip"

    report = package_script(PRESET_PATH, output)

    assert report["passed"] is True
    assert report["manifest"]["preset_file"] == PRESET_PATH.name
    assert output.exists()
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert "werewolf_v1_guard.yaml" in names
        assert PRESET_PATH.name in names
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        simulation = json.loads(archive.read("simulation_report.json").decode("utf-8"))
    assert manifest["preset_file"] == PRESET_PATH.name
    assert manifest["params"]["total_players"] == 12
    assert simulation["preset_path"].endswith(PRESET_PATH.name)


def test_all_script_presets_validate_and_simulate() -> None:
    """All checked-in script presets should resolve to playable scripts."""
    preset_paths = sorted(
        path for path in PRESET_DIR.rglob("*.preset.yaml")
        if not path.name.startswith("._")
    )
    assert preset_paths, "scripts/presets 应至少包含一个 preset"

    for preset_path in preset_paths:
        validation = validate_script(preset_path)
        simulation = simulate_script(preset_path)

        assert validation.passed(), f"{preset_path} validation failed: {validation.to_dict()}"
        assert simulation["passed"] is True, f"{preset_path} simulation failed: {simulation}"


def test_invalid_preset_path_reports_cli_error(tmp_path: Path) -> None:
    """Invalid preset targets should fail as CLI errors, not raw tracebacks."""
    preset_path = tmp_path / "bad.preset.yaml"
    preset_path.write_text(
        "name: bad\nscript: missing/script.yaml\nparams: {}\n",
        encoding="utf-8",
    )

    with pytest.raises(CliError, match="preset 解析失败"):
        validate_script(preset_path)


def test_run_cli_validate_and_package(tmp_path: Path, capsys) -> None:
    """CLI entry function should return process-like exit codes."""
    validate_code = run_cli(["validate", str(SCRIPT_PATH), "--json"])
    validate_output = json.loads(capsys.readouterr().out)

    package_path = tmp_path / "game.zip"
    package_code = run_cli(["package", str(SCRIPT_PATH), "--output", str(package_path), "--json"])
    package_output = json.loads(capsys.readouterr().out)

    assert validate_code == 0
    assert validate_output["passed"] is True
    assert package_code == 0
    assert package_output["passed"] is True
    assert package_path.exists()


def test_core_script_matrix_validates_previews_and_simulates() -> None:
    """All official core scripts should pass the publish-tool checks."""
    script_paths = sorted(path for path in SCRIPT_DIR.glob("*.yaml") if not path.name.startswith("._"))
    assert script_paths, "core scripts 目录应至少包含一个正式脚本"

    for script_path in script_paths:
        validation = validate_script(script_path)
        preview = preview_script(script_path)
        simulation = simulate_script(script_path)

        assert validation.passed(), f"{script_path.name} validation failed: {validation.to_dict()}"
        assert preview["issues"]["passed"] is True, f"{script_path.name} preview failed"
        assert simulation["passed"] is True, f"{script_path.name} simulation failed: {simulation}"


def test_named_games_from_plan_have_official_scripts() -> None:
    """Plan-named game examples should have first-class script files."""
    existing = {path.name for path in SCRIPT_DIR.glob("*.yaml") if not path.name.startswith("._")}

    missing = sorted(REQUIRED_NAMED_GAME_SCRIPTS - existing)

    assert missing == []


def test_core_deep_rule_scripts_are_playable_round_loops() -> None:
    """重点深规则脚本应是可重复玩的多阶段流程，而不是单步示例。"""
    for name in [
        "uno_lite.yaml",
        "exploding_kittens_lite.yaml",
        "gomoku_lite.yaml",
        "xiangqi_lite.yaml",
        "go_lite.yaml",
        "checkers_lite.yaml",
        "monopoly_lite.yaml",
        "flight_chess_lite.yaml",
        "texas_holdem_party_lite.yaml",
        "card_event_party_lite.yaml",
        "dice_map_adventure_lite.yaml",
        "asset_trading_party_lite.yaml",
        "dnd_fixed_adventure.yaml",
        "coc_fixed_mystery.yaml",
        "story_campaign_lite.yaml",
        "text_adventure_lite.yaml",
        "agent_dm_adventure_lite.yaml",
    ]:
        doc = yaml.safe_load((SCRIPT_DIR / name).read_text(encoding="utf-8"))
        scenes = doc["flow"]["scenes"]
        effects = [
            effect
            for scene in scenes
            for effect in (scene.get("resolution", {}).get("effects") or [])
        ]
        views = [
            view
            for scene in scenes
            for view in (scene.get("publication", {}).get("views") or [])
        ]

        assert doc["flow"]["loop"] is True, name
        assert len(scenes) >= 3, name
        assert any(effect.get("type") == "rule_set_apply" for effect in effects), name
        assert views, name
