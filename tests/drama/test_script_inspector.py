"""ScriptInspector runtime/publish inspection 测试（interactive_session 版）。

旧的 game_session 发布检查（concepts/scene_type/rule_set-missing-extension）已随旧
runtime 删除；本测试改为覆盖迁移后的 interactive_session 脚本检查。
"""

from pathlib import Path

from drama_engine.application.script_inspector import ScriptInspector

GOMOKU = Path("drama_engine/scripts/interactive_session/board/gomoku.yaml")
WEREWOLF = Path("drama_engine/scripts/interactive_session/deduction/werewolf.yaml")


def test_script_inspector_exposes_runtime_for_interactive_session():
    """Inspector 应把 interactive_session 脚本识别为已注册 runtime，并数出 scene。"""
    result = ScriptInspector().inspect_file(str(GOMOKU))

    assert result["runtime"]["type"] == "interactive_session"
    assert result["runtime"]["registered"] is True
    assert result["overview"]["runtime_type"] == "interactive_session"
    # 五子棋有黑白两个落子 scene
    assert result["overview"]["scene_count"] >= 2
    # 校验通过
    assert result["issues"]["passed"] is True


def test_script_inspector_reports_scenes_for_werewolf():
    """狼人杀脚本应被数出多幕，并通过校验。"""
    result = ScriptInspector().inspect_file(str(WEREWOLF))

    assert result["runtime"]["type"] == "interactive_session"
    assert result["overview"]["scene_count"] >= 4
    assert result["issues"]["passed"] is True
