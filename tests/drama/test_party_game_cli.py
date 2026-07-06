"""Party Game DSL CLI 测试（interactive_session 版）。

旧的 game_session/preset/官方脚本矩阵已随旧 runtime 删除并归档到 legacy_scripts；
本测试改为覆盖迁移后的 interactive_session 代表脚本。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from drama_engine.cli import CliError, package_script, preview_script, run_cli, simulate_script, validate_script

# 迁移后的代表脚本（interactive_session）。
SCRIPT_DIR = Path("drama_engine/scripts/interactive_session")
GOMOKU = SCRIPT_DIR / "board" / "gomoku.yaml"
WEREWOLF = SCRIPT_DIR / "deduction" / "werewolf.yaml"
WEREWOLF_12P = SCRIPT_DIR / "deduction" / "werewolf_12p_guard.yaml"
UNDERCOVER = SCRIPT_DIR / "deduction" / "who_is_undercover.yaml"
UNO = SCRIPT_DIR / "cards" / "uno.yaml"
MONOPOLY = SCRIPT_DIR / "economy" / "monopoly.yaml"

REPRESENTATIVE_SCRIPTS = [GOMOKU, WEREWOLF, WEREWOLF_12P, UNDERCOVER, UNO, MONOPOLY]


def test_validate_preview_and_simulate_gomoku() -> None:
    """CLI helpers 对五子棋应产出可用报告，runtime 为 interactive_session。"""
    validation = validate_script(GOMOKU)
    preview = preview_script(GOMOKU)
    simulation = simulate_script(GOMOKU)

    assert validation.passed()
    assert preview["overview"]["scene_count"] > 0
    assert preview["issues"]["passed"] is True
    assert simulation["passed"] is True
    assert simulation["runtime_type"] == "interactive_session"
    assert simulation["scene_count"] > 0


def test_all_representative_scripts_validate_and_simulate() -> None:
    """所有迁移代表脚本都应通过 validate + simulate，且为 interactive_session。"""
    for script_path in REPRESENTATIVE_SCRIPTS:
        validation = validate_script(script_path)
        simulation = simulate_script(script_path)
        assert validation.passed(), f"{script_path.name} validation failed: {validation.to_dict()}"
        assert simulation["passed"] is True, f"{script_path.name} simulation failed: {simulation}"
        assert simulation["runtime_type"] == "interactive_session"


def test_script_library_only_contains_interactive_session() -> None:
    """脚本库应只剩 interactive_session 脚本（旧 runtime 已归档到 legacy_scripts）。"""
    import yaml

    scripts = [p for p in SCRIPT_DIR.rglob("*.yaml") if not p.name.startswith("._")]
    assert scripts, "interactive_session 脚本库不应为空"
    for path in scripts:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        runtime = doc.get("runtime")
        runtime_type = runtime.get("type") if isinstance(runtime, dict) else runtime
        assert runtime_type == "interactive_session", f"{path} 不是 interactive_session"


def test_package_script_writes_publish_zip(tmp_path: Path) -> None:
    """package 命令应打出含 DSL/校验/模拟/预览的 zip。"""
    output = tmp_path / "gomoku_package.zip"
    report = package_script(GOMOKU, output)

    assert report["passed"] is True
    assert output.exists()
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert GOMOKU.name in names
        assert "manifest.json" in names
        assert "validation_report.json" in names
        assert "simulation_report.json" in names
        assert "preview.json" in names
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    assert manifest["validation_passed"] is True
    assert manifest["simulation_passed"] is True


def test_invalid_preset_path_reports_cli_error(tmp_path: Path) -> None:
    """非法 preset 目标应报 CliError，而不是裸 traceback。"""
    preset_path = tmp_path / "bad.preset.yaml"
    preset_path.write_text(
        "name: bad\nscript: missing/script.yaml\nparams: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(CliError, match="preset 解析失败"):
        validate_script(preset_path)


def test_run_cli_validate_and_package(tmp_path: Path, capsys) -> None:
    """CLI 入口函数应返回进程式退出码。"""
    validate_code = run_cli(["validate", str(GOMOKU), "--json"])
    validate_output = json.loads(capsys.readouterr().out)

    package_path = tmp_path / "game.zip"
    package_code = run_cli(["package", str(GOMOKU), "--output", str(package_path), "--json"])
    package_output = json.loads(capsys.readouterr().out)

    assert validate_code == 0
    assert validate_output["passed"] is True
    assert package_code == 0
    assert package_output["passed"] is True
    assert package_path.exists()
