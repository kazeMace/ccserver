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


def test_dsl_registry_allows_view_and_input_registration():
    """扩展可以注册输入组件和视图类型。"""
    registry = build_default_dsl_registry()
    registry.register_input_widget("timeline_select")
    registry.register_view_kind("timeline")

    assert registry.has_input_widget("timeline_select")
    assert registry.has_view_kind("timeline")

