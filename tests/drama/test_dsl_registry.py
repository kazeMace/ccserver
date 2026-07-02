"""DSL registry tests."""

import pytest

from drama_engine.core.dsl import ActionPolicySpec, SceneTypeSpec, build_default_dsl_registry


def test_default_dsl_registry_matches_core_names():
    """默认注册表包含当前内置 DSL 名称。"""
    registry = build_default_dsl_registry()

    assert "speak" in registry.scene_type_names()
    assert "sequential" in registry.dialogue_policy_names()
    assert "vote" in registry.action_policy_names()
    assert "structured" in registry.response_mode_names()
    assert "target" in registry.response_schema_names()
    assert "player_select" in registry.input_widget_names()
    assert "key-value" in registry.view_kind_names()


def test_default_dsl_registry_provides_scene_defaults():
    """scene_type 默认策略来自注册表。"""
    registry = build_default_dsl_registry()

    assert registry.default_dialogue_mode("vote") == "simultaneous"
    assert registry.default_action_kind("choose") == "choose_one"
    assert registry.default_response_mode("narration") == "none"
    assert registry.default_response_schema("board_move") == "move"


def test_dsl_registry_allows_registration():
    """扩展可以注册新的 DSL 名称。"""
    registry = build_default_dsl_registry()
    registry.register_dialogue_policy("custom_dialogue")
    registry.register_action_policy(ActionPolicySpec("custom_action", "custom"))
    registry.register_scene_type(SceneTypeSpec("custom_scene", "custom_dialogue", "custom_action", "structured"))

    assert registry.has_dialogue_policy("custom_dialogue")
    assert registry.has_action_policy("custom_action")
    assert registry.has_scene_type("custom_scene")
    assert registry.default_dialogue_mode("custom_scene") == "custom_dialogue"
    assert registry.default_action_kind("custom_scene") == "custom_action"


def test_dsl_registry_dialogue_policy_factory():
    """dialogue policy factory 可注册并创建运行时对象。"""
    registry = build_default_dsl_registry()
    registry.set_dialogue_policy_factory("sequential", lambda spec: {"mode": spec.get("mode")})

    policy = registry.create_dialogue_policy("sequential", {"mode": "sequential"})

    assert policy == {"mode": "sequential"}


def test_dsl_registry_response_schema_factory():
    """response schema factory 可注册并创建响应模型。"""
    registry = build_default_dsl_registry()
    registry.set_response_schema_factory(
        "target",
        lambda response_spec, action_policy: {
            "schema": response_spec.get("schema"),
            "action": action_policy.get("kind"),
        },
    )

    model = registry.create_response_model("target", {"schema": "target"}, {"kind": "choose_one"})

    assert model == {"schema": "target", "action": "choose_one"}


def test_dsl_registry_response_schema_factory_requires_registered_schema():
    """response schema factory 只能绑定到已注册 schema。"""
    registry = build_default_dsl_registry()

    with pytest.raises(AssertionError):
        registry.set_response_schema_factory("unknown_schema", lambda response_spec, action_policy: object())


def test_dsl_registry_response_schema_factory_reports_missing_factory():
    """已注册但未设置 factory 的 response schema 会明确报错。"""
    registry = build_default_dsl_registry()

    with pytest.raises(ValueError, match="response schema 尚未设置 factory"):
        registry.create_response_model("target", {"schema": "target"}, {"kind": "choose_one"})

def test_dsl_registry_allows_view_and_input_registration():
    """扩展可以注册输入组件和视图类型。"""
    registry = build_default_dsl_registry()
    registry.register_input_widget("timeline_select")
    registry.register_view_kind("timeline")

    assert registry.has_input_widget("timeline_select")
    assert registry.has_view_kind("timeline")

