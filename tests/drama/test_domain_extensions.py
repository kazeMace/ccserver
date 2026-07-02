"""Domain extension registry tests."""

from drama_engine.core.dsl.extensions import build_default_domain_extension_registry


def test_default_domain_extension_registry_lists_core_domains():
    """默认 domain extension registry 应声明通用领域能力。"""
    registry = build_default_domain_extension_registry()

    assert {"board", "cards", "dice", "economy", "story"}.issubset(set(registry.names()))
    board = registry.describe("board")
    assert board["name"] == "board"
    assert "move_action" in board["capabilities"]
