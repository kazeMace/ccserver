"""DSL schema/capability discovery tests."""

from drama_engine.core.dsl.schema import build_core_dsl_capabilities, build_core_dsl_schema


def test_core_dsl_capabilities_export_registry_values():
    """capabilities 应导出 runtime、dialogue、scene、view/input 等注册能力。"""
    doc = build_core_dsl_capabilities()
    capabilities = doc["capabilities"]

    runtime_names = {item["name"] for item in capabilities["runtime_types"]}
    scene_defaults = {
        item["name"]: item["defaults"]
        for item in capabilities["scene_types"]
    }

    assert runtime_names == {"interactive_session"}
    assert "openchat" in capabilities["dialogue_policies"]
    assert scene_defaults["vote"]["dialogue_policy.mode"] == "simultaneous"
    assert "player_select" in capabilities["input_widgets"]
    assert "key-value" in capabilities["view_kinds"]
    extension_names = {item["name"] for item in capabilities["domain_extensions"]}
    game_pack_plugins = {item["plugin"] for item in capabilities["game_packs"]}
    rule_set_plugins = {item["plugin"] for item in capabilities["rule_sets"]}
    assert {"board", "cards", "story"}.issubset(extension_names)
    assert "builtin.party.free_discussion" in game_pack_plugins
    assert "builtin.board.generic" in rule_set_plugins
    # authoring_templates 已从 core capabilities 移除（M7.2 消除 core→application 反向依赖）：
    # 它属于 application 层能力，由 application 层调用方自行补充，core 不再导出。
    assert "authoring_templates" not in capabilities


def test_core_dsl_schema_exports_scene_fields():
    """schema 应导出 scene/runtime 的机器可读字段。"""
    doc = build_core_dsl_schema()
    scene_fields = {
        field["name"]: field
        for field in doc["schemas"]["scene"]["fields"]
    }

    assert doc["schemas"]["meta"]["name"] == "meta"
    assert doc["schemas"]["runtime"]["name"] == "runtime"
    assert doc["schemas"]["publish"]["name"] == "publish"
    assert doc["schemas"]["players"]["name"] == "players"
    assert doc["schemas"]["role"]["name"] == "role"
    assert doc["schemas"]["scope"]["name"] == "scope"
    assert doc["schemas"]["flow"]["name"] == "flow"
    assert doc["schemas"]["publication"]["name"] == "publication"
    assert doc["schemas"]["extensions"]["name"] == "extensions"
    assert doc["schemas"]["game_pack"]["name"] == "game_pack"
    assert doc["schemas"]["rule_set"]["name"] == "rule_set"
    assert scene_fields["name"]["required"] is True
    assert scene_fields["dialogue_policy"]["type"] == "object"
    assert scene_fields["participants"]["type"] == "selector"


def test_core_dsl_schema_export_is_stable():
    """schema 包导出应返回当前 core DSL schema。"""
    from drama_engine.core.dsl.schema import build_core_dsl_schema as exported_build_core_dsl_schema

    assert exported_build_core_dsl_schema() == build_core_dsl_schema()
