"""Game pack / rule set registry tests."""

from drama_engine.core.dsl.game_packs import (
    build_default_game_pack_registry,
    build_default_rule_set_registry,
)


def test_default_game_pack_registry_lists_builtin_example():
    """默认 game_pack registry 应包含内置示例包。"""
    registry = build_default_game_pack_registry()

    assert registry.has("builtin.party.free_discussion")
    item = registry.describe("builtin.party.free_discussion")
    assert item["plugin"] == "builtin.party.free_discussion"
    assert "game_session" in item["supported_runtimes"]


def test_default_rule_set_registry_lists_generic_domains():
    """默认 rule_set registry 应声明通用领域规则接口。"""
    registry = build_default_rule_set_registry()

    assert registry.has("builtin.board.generic")
    assert registry.has("builtin.cards.generic")
    assert registry.describe("builtin.board.generic")["required_extensions"] == ["board"]


def test_default_rule_set_registry_lists_named_lite_games():
    """文档点名的 Lite 游戏应有具体 rule_set 元数据。"""
    registry = build_default_rule_set_registry()

    expected = {
        "builtin.board.gomoku_lite",
        "builtin.board.xiangqi_lite",
        "builtin.board.go_lite",
        "builtin.board.checkers_lite",
        "builtin.board.flight_chess_lite",
        "builtin.board.monopoly_lite",
        "builtin.cards.uno_lite",
        "builtin.cards.exploding_kittens_lite",
        "builtin.cards.texas_holdem_party_lite",
        "builtin.cards.card_event_party_lite",
        "builtin.story.dice_map_adventure_lite",
        "builtin.story.dnd_fixed_adventure",
        "builtin.story.coc_fixed_mystery",
        "builtin.story.campaign_lite",
        "builtin.story.text_adventure_lite",
        "builtin.story.agent_dm_adventure_lite",
        "builtin.economy.asset_trading_lite",
    }

    assert expected.issubset(set(registry.names()))
