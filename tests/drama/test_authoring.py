"""UGC authoring tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from drama_engine.cli import author_script, run_cli


@pytest.mark.parametrize(
    ("idea", "expected_type", "expected_runtime"),
    [
        ("做一个 12 人狼人身份推理游戏，有夜晚行动和白天投票", "social_deduction", "interactive_session"),
        ("做一个四人 UNO 卡牌游戏，玩家摸牌出牌，先清空手牌获胜", "card_game", "interactive_session"),
        ("做一个双人五子棋棋盘游戏，轮流落子", "board_game", "interactive_session"),
        ("做一个大富翁地图经济游戏，有骰子、资产和交易", "map_economy", "interactive_session"),
        ("做一个多 Agent 圆桌群聊，讨论新游戏创意", "group_chat", "interactive_session"),
        ("做一个 DND 动态剧情冒险，由 DM 推进故事", "story", "interactive_session"),
    ],
)
def test_authoring_acceptance_paths_generate_valid_scripts(
    tmp_path: Path,
    idea: str,
    expected_type: str,
    expected_runtime: str,
) -> None:
    """Plan acceptance paths should generate validated scripts."""
    output = tmp_path / f"{expected_type}.yaml"

    report = author_script(idea=idea, output_path=output)

    assert report["kind"] == "author"
    assert report["game_type"] == expected_type
    assert report["runtime_type"] == expected_runtime
    assert report["validation"]["passed"] is True
    assert report["simulation"]["passed"] is True
    assert report["preview"]["issues"]["passed"] is True
    assert output.exists()


def test_authoring_can_create_publish_package(tmp_path: Path) -> None:
    """Authoring should run through package when package output is requested."""
    output = tmp_path / "card_game.yaml"
    package_output = tmp_path / "card_game.zip"

    report = author_script(
        idea="做一个卡牌游戏，玩家摸牌出牌",
        output_path=output,
        package_path=package_output,
    )

    assert report["validation"]["passed"] is True
    assert report["package"]["passed"] is True
    assert package_output.exists()


def test_run_cli_author_outputs_json(tmp_path: Path, capsys) -> None:
    """CLI author command should return JSON and write the generated YAML."""
    output = tmp_path / "board_game.yaml"

    exit_code = run_cli(["author", "做一个棋盘游戏", "--output", str(output), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["kind"] == "author"
    assert payload["game_type"] == "board_game"
    assert payload["validation"]["passed"] is True
    assert output.exists()


def test_run_cli_author_checklist_does_not_write_yaml(tmp_path: Path, capsys) -> None:
    """Checklist mode should expose questions without creating output files."""
    output = tmp_path / "unused.yaml"

    exit_code = run_cli(["author", "做一个群聊", "--output", str(output), "--checklist", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["kind"] == "author_checklist"
    assert payload["game_type"] == "group_chat"
    assert payload["required_questions"]
    assert not output.exists()
