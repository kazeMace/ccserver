"""metadata/publish validation tests."""

import yaml

from drama_engine.core.dsl.compiler import YamlCompiler
from drama_engine.core.dsl.validator import DslValidator


def _base_doc():
    return {
        "meta": {"title": "测试"},
        "concepts": {
            "roles": {"v": {"display_name": "村民", "description": "普通玩家"}},
            "factions": {"good": {"display_name": "好人", "description": "好人阵营"}},
            "scopes": {"public": {"display_name": "公开", "description": "全员可见"}},
        },
        "roles": [{"name": "v", "display_name": "村民", "faction": "good", "brief": "b"}],
        "players": {"count": 1, "casting": {"type": "shuffle", "distribution": {"v": 1}}},
        "scopes": [{"name": "public", "members": "all"}],
        "flow": {
            "loop": False,
            "scenes": [{"name": "chat", "scene_type": "speak", "scope": "public", "participants": "all"}],
        },
        "referee": {"win_conditions": []},
    }


def test_validate_meta_and_publish_accepts_ugc_fields():
    """meta/publish 应接受 UGC 发布元信息。"""
    compiler = YamlCompiler()
    doc = _base_doc()
    doc["meta"] = {
        "id": "party_test",
        "name": "party_test",
        "display_name": "派对测试",
        "title": "派对测试",
        "version": "0.1.0",
        "author": "tester",
        "description": "desc",
        "tags": ["party", "test"],
        "locale": "zh-CN",
        "license": "MIT",
    }
    doc["publish"] = {
        "id": "party_test",
        "version": "0.1.0",
        "visibility": "private",
        "tags": ["party"],
        "required_extensions": [],
        "license": "MIT",
    }

    errors = compiler.validate(doc)
    assert errors == []
    script = compiler.compile_doc(doc)
    assert script.publish["id"] == "party_test"


def test_validate_meta_and_publish_type_errors():
    """meta/publish 字段类型错误应清晰报错。"""
    compiler = YamlCompiler()
    doc = _base_doc()
    doc["meta"] = {"tags": "bad", "version": 1}
    doc["publish"] = {
        "id": 1,
        "visibility": "friends",
        "tags": "bad",
        "required_extensions": "board",
    }

    errors = compiler.validate(doc)

    assert any("meta 必须至少包含" in e for e in errors)
    assert any("meta.version" in e for e in errors)
    assert any("meta.tags" in e for e in errors)
    assert any("publish.id" in e for e in errors)
    assert any("publish.visibility" in e for e in errors)
    assert any("publish.tags" in e for e in errors)
    assert any("publish.required_extensions" in e for e in errors)


def test_dsl_validator_reports_publish_and_extension_registry_errors():
    """DslValidator 应直接报告发布、扩展、game_pack 和 rule_set 注册问题。"""
    doc = _base_doc()
    doc["extensions"] = {
        "unknown_domain": {"enabled": True},
    }
    doc["game_pack"] = {"plugin": "marketplace.unknown"}
    doc["rule_set"] = {"plugin": "builtin.cards.generic"}
    doc["publish"] = {
        "visibility": "friends",
        "required_extensions": ["cards", "unknown_domain"],
    }

    report = DslValidator().validate_text(yaml.safe_dump(doc, allow_unicode=True))
    codes = {issue.code for issue in report.issues}

    assert "UNKNOWN_EXTENSION" in codes
    assert "UNKNOWN_GAME_PACK" in codes
    assert "RULE_SET_MISSING_EXTENSION" in codes
    assert "INVALID_PUBLISH_VISIBILITY" in codes
    assert "UNKNOWN_PUBLISH_REQUIRED_EXTENSION" in codes


def test_dsl_validator_export_returns_validator_class():
    """validator 包导出应返回当前 validator 类。"""
    from drama_engine.core.dsl.validator import DslValidator as ExportedDslValidator

    assert ExportedDslValidator is DslValidator
