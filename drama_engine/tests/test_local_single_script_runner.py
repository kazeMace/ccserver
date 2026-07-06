"""Tests for the local single-script Drama Engine runner."""

from __future__ import annotations

import pytest

from drama_engine.run_script import build_parser, parse_cli_params, resolve_script_path, run_local_script


def test_parse_cli_params_coerces_basic_values() -> None:
    """CLI 参数应支持基础类型推断。"""
    params = parse_cli_params([
        "total_players=5",
        "dry=true",
        "ratio=1.5",
        "names=Player_1,Player_2",
    ])

    assert params == {
        "total_players": 5,
        "dry": True,
        "ratio": 1.5,
        "names": ["Player_1", "Player_2"],
    }


def test_resolve_script_path_accepts_library_script() -> None:
    """本地入口应能解析新版 scripts 分类路径。"""
    path = resolve_script_path("drama_engine/scripts/interactive_session/deduction/werewolf_12p_guard.yaml")

    assert path.name == "werewolf_12p_guard.yaml"
    assert path.exists()


def test_parser_uses_dashboard_by_default() -> None:
    """默认应进入 dashboard 演示模式，而不是直接 headless 跑完。"""
    parsed = build_parser().parse_args(["drama_engine/scripts/interactive_session/deduction/werewolf_12p_guard.yaml"])

    assert parsed.headless is False
    assert parsed.host == "127.0.0.1"
    assert parsed.port == 8766
    assert parsed.no_open is False


@pytest.mark.asyncio
async def test_headless_runner_can_finish_dry_run() -> None:
    """显式 --headless 时，本地单脚本 runner 应复用真实 runner 并跑完 dry-run。

    用谁是卧底做端到端 dry-run 样例：它的 speak→vote→出局→判胜链路可在 MockActor 下
    完整跑到 ended（棋牌/狼人等策略性玩法需要真实 LLM 才能产出有效走子，不适合 mock 跑完）。
    """
    exit_code = await run_local_script([
        "drama_engine/scripts/interactive_session/deduction/who_is_undercover.yaml",
        "--headless",
        "--dry-run",
        "--print-events",
        "none",
        "--log-level",
        "WARNING",
    ])

    assert exit_code == 0
