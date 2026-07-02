"""ScriptInspector publish/runtime inspection tests."""

import yaml

from drama_engine.application.script_inspector import ScriptInspector


def _script_doc() -> dict:
    """Build a minimal publishable script document."""
    return {
        "meta": {"title": "发布检查"},
        "concepts": {
            "roles": {"v": {"display_name": "村民", "description": "普通玩家"}},
            "factions": {"good": {"display_name": "好人", "description": "好人阵营"}},
            "scopes": {"public": {"display_name": "公开", "description": "所有人可见"}},
        },
        "runtime": {"type": "game_session", "config": {"mode": "test"}},
        "extensions": {"board": {"enabled": True, "config": {"size": 15}}},
        "game_pack": {"plugin": "builtin.party.free_discussion"},
        "rule_set": {"plugin": "builtin.board.generic"},
        "publish": {
            "id": "publish_check",
            "version": "0.1.0",
            "visibility": "private",
            "required_extensions": ["board"],
        },
        "roles": [{"name": "v", "display_name": "村民", "faction": "good", "brief": "b"}],
        "players": {"count": 1, "casting": {"type": "shuffle", "distribution": {"v": 1}}},
        "scopes": [{"name": "public", "members": "all"}],
        "flow": {
            "loop": False,
            "scenes": [
                {
                    "name": "open",
                    "scene_type": "narration",
                    "scope": "public",
                    "publication": {
                        "messages": [{"audience": "public", "text": "开始"}],
                        "views": [{"id": "board", "kind": "board", "title": "棋盘"}],
                    },
                }
            ],
        },
        "referee": {"win_conditions": []},
    }


def test_script_inspector_exposes_runtime_and_publish_readiness():
    """Inspector 应输出 runtime、扩展、规则集和发布检查摘要。"""
    raw_text = yaml.safe_dump(_script_doc(), allow_unicode=True)

    result = ScriptInspector().inspect_text(raw_text)

    assert result["runtime"]["type"] == "game_session"
    assert result["runtime"]["registered"] is True
    assert result["overview"]["runtime_type"] == "game_session"
    assert result["overview"]["extension_count"] == 1
    assert result["extensions"][0]["name"] == "board"
    assert result["extensions"][0]["registered"] is True
    assert "move_action" in result["extensions"][0]["capabilities"]
    assert result["game_pack"]["registered"] is True
    assert result["rule_set"]["plugin"] == "builtin.board.generic"
    assert result["rule_set"]["missing_extensions"] == []
    assert result["publish"]["id"] == "publish_check"
    assert result["publish_inspection"]["ready"] is True


def test_script_inspector_publish_inspection_reports_missing_extension():
    """Inspector publish_inspection 应汇总发布阻断和缺失扩展。"""
    doc = _script_doc()
    doc["extensions"] = {}
    raw_text = yaml.safe_dump(doc, allow_unicode=True)

    result = ScriptInspector().inspect_text(raw_text)

    assert result["publish_inspection"]["ready"] is False
    assert "board" in result["publish_inspection"]["missing_required_extensions"]
    assert "RULE_SET_MISSING_EXTENSION" in result["publish_inspection"]["blocking_issue_codes"]
