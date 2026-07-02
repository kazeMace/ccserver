"""rule_set apply effect tests."""

from drama_engine.core.dsl.compiler import YamlCompiler
from drama_engine.core.engine import State, StateWriter, Vocabulary
from drama_engine.core.dsl.plugins import BuiltinPartyRuleSetPlugin, RuleSetContext


def _state() -> State:
    """创建 rule_set 单元测试使用的开放状态。"""
    return State(Vocabulary(
        roles=frozenset({"black", "white", "red", "dark", "light", "player", "tycoon", "poker_player"}),
        factions=frozenset({"solo", "black_side", "white_side"}),
        scopes=frozenset({"public"}),
        abilities=frozenset(),
    ))


def _apply_rule_set(state: State, responses: list[dict], plugin: str, config: dict | None = None) -> dict:
    """直接执行内置派对游戏 rule_set handler。"""
    writer = StateWriter(state)
    rule_set = {"plugin": plugin, "config": config or {}}
    context = RuleSetContext(
        state=state,
        writer=writer,
        responses=responses,
        rule_set=rule_set,
        effect={},
    )
    engine = BuiltinPartyRuleSetPlugin()
    if plugin.startswith("builtin.board."):
        return engine._apply_board(context)
    if plugin.startswith("builtin.cards."):
        return engine._apply_cards(context)
    if plugin.startswith("builtin.story."):
        return engine._apply_story(context)
    if plugin.startswith("builtin.economy."):
        return engine._apply_economy(context)
    raise AssertionError(f"unsupported test plugin: {plugin}")


def test_rule_set_apply_effect_writes_generic_result():
    """rule_set_apply 应调用当前 rule_set handler 并写入结果。"""
    compiler = YamlCompiler()
    doc = {
        "meta": {"title": "测试"},
        "extensions": {"board": {"enabled": True}},
        "rule_set": {"plugin": "builtin.board.generic", "config": {}},
        "concepts": {
            "roles": {"player": {"display_name": "玩家", "description": "玩家"}},
            "factions": {"neutral": {"display_name": "中立", "description": "中立"}},
            "scopes": {"public": {"display_name": "公开", "description": "公开"}},
        },
        "roles": [{"name": "player", "display_name": "玩家", "faction": "neutral", "brief": "b"}],
        "players": {"count": 1, "casting": {"type": "shuffle", "distribution": {"player": 1}}},
        "scopes": [{"name": "public", "members": "all"}],
        "flow": {
            "loop": False,
            "scenes": [{
                "name": "board-move",
                "scene_type": "board",
                "scope": "public",
                "participants": "all",
                "dialogue_policy": {"mode": "single"},
                "response": {"mode": "structured", "schema": "move", "cue": "move"},
                "resolution": {
                    "effects": [{
                        "type": "rule_set_apply",
                        "result_path": "GAME.last_rule_result",
                    }]
                },
            }],
        },
        "referee": {"win_conditions": []},
    }

    errors = compiler.validate(doc)
    assert errors == []
    script = compiler.compile_doc(doc)
    scene = script.flow.scenes[0]
    state = State(script.vocab)
    state.register_entity("GAME", {})
    writer = StateWriter(state)

    scene.on_result(
        [{"actor": "Player_1", "text": "move", "data": {"move": {"position": [0, 0]}}}],
        state,
        writer,
    )

    result = state.get_attr("GAME", "last_rule_result")
    assert result["plugin"] == "builtin.board.generic"
    assert result["accepted"] is True
    assert result["action"] == {"move": {"position": [0, 0]}}


def test_specific_rule_set_apply_writes_domain_result():
    """具体 Lite rule_set 应返回领域化结果并写入通用追踪状态。"""
    compiler = YamlCompiler()
    doc = {
        "meta": {"title": "五子棋测试"},
        "extensions": {"board": {"enabled": True}},
        "rule_set": {"plugin": "builtin.board.gomoku_lite", "config": {}},
        "concepts": {
            "roles": {"black": {"display_name": "黑棋", "description": "黑棋"}},
            "factions": {"black_side": {"display_name": "黑方", "description": "黑方"}},
            "scopes": {"public": {"display_name": "公开", "description": "公开"}},
        },
        "roles": [{"name": "black", "display_name": "黑棋", "faction": "black_side", "brief": "b"}],
        "players": {"count": 1, "casting": {"type": "shuffle", "distribution": {"black": 1}}},
        "scopes": [{"name": "public", "members": "all"}],
        "flow": {
            "loop": False,
            "scenes": [{
                "name": "black-move",
                "scene_type": "board",
                "scope": "public",
                "participants": "all",
                "dialogue_policy": {"mode": "single"},
                "response": {"mode": "structured", "schema": "move", "cue": "move"},
                "resolution": {
                    "effects": [{
                        "type": "rule_set_apply",
                        "result_path": "GAME.last_rule_result",
                    }]
                },
            }],
        },
        "referee": {"win_conditions": []},
    }

    errors = compiler.validate(doc)
    assert errors == []
    script = compiler.compile_doc(doc)
    scene = script.flow.scenes[0]
    state = State(script.vocab)
    state.register_entity("GAME", {})
    writer = StateWriter(state)

    scene.on_result(
        [{"actor": "Player_1", "text": "move", "data": {"move": {"position": [7, 7]}}}],
        state,
        writer,
    )

    result = state.get_attr("GAME", "last_rule_result")
    tracked = state.get_attr("GAME", "last_rule_set_result")
    assert result["plugin"] == "builtin.board.gomoku_lite"
    assert result["game"] == "gomoku_lite"
    assert result["objective"] == "five_in_row"
    assert result["accepted"] is True
    assert tracked == result


def test_gomoku_rule_set_rejects_occupied_cell_and_detects_five_in_row():
    """五子棋 rule_set 应写入棋盘、拒绝重复落子，并识别五连胜利。"""
    state = _state()
    state.register_entity("GAME", {"gomoku_board": {"7,3": "B", "7,4": "B", "7,5": "B", "7,6": "B"}})
    state.register_entity("Player_1", {"role": "black", "alive": True})
    state.register_entity("Player_2", {"role": "white", "alive": True})

    blocked = _apply_rule_set(
        state,
        [{"actor": "Player_2", "data": {"move": {"position": [7, 3]}}}],
        "builtin.board.gomoku_lite",
    )
    assert blocked["accepted"] is False
    assert "已经有棋子" in blocked["reason"]

    result = _apply_rule_set(
        state,
        [{"actor": "Player_1", "data": {"move": {"position": [7, 7]}}}],
        "builtin.board.gomoku_lite",
    )

    assert result["accepted"] is True
    assert result["winner"] == "Player_1"
    assert result["line"] == 5
    assert state.get_attr("GAME", "winner") == "Player_1"
    assert state.get_attr("GAME", "gomoku_board")["7,7"] == "B"


def test_exploding_kittens_rule_set_defuses_or_eliminates_player():
    """炸弹猫 rule_set 应在摸到炸弹时优先拆除，否则淘汰玩家。"""
    state = _state()
    state.register_entity("GAME", {"kitten_draw_pile": ["exploding-kitten", "skip"]})
    state.register_entity("Player_1", {"role": "player", "alive": True, "hand": ["defuse"], "defuse_count": 0})
    state.register_entity("Player_2", {"role": "player", "alive": True, "hand": [], "defuse_count": 0})

    defused = _apply_rule_set(
        state,
        [{"actor": "Player_1", "data": {"card_action": {"type": "draw"}}}],
        "builtin.cards.exploding_kittens_lite",
    )
    assert defused["defused"] is True
    assert state.get_attr("Player_1", "alive") is True
    assert "exploding-kitten" in state.get_attr("GAME", "kitten_draw_pile")

    state = _state()
    state.register_entity("GAME", {"kitten_draw_pile": ["exploding-kitten"]})
    state.register_entity("Player_2", {"role": "player", "alive": True, "hand": [], "defuse_count": 0})
    exploded = _apply_rule_set(
        state,
        [{"actor": "Player_2", "data": {"card_action": {"type": "draw"}}}],
        "builtin.cards.exploding_kittens_lite",
    )
    assert exploded["exploded"] is True
    assert state.get_attr("Player_2", "alive") is False
    assert state.get_attr("GAME", "last_exploded_player") == "Player_2"


def test_flight_chess_rule_set_requires_six_for_takeoff_and_hits_plane():
    """飞行棋 rule_set 应要求 6 点起飞，并把撞到的飞机送回基地。"""
    state = _state()
    state.register_entity("GAME", {})
    state.register_entity("Player_1", {"role": "player", "planes": {"A": "base"}, "planes_home": 0})
    state.register_entity("Player_2", {"role": "player", "planes": {"A": 4}, "planes_home": 0})

    blocked = _apply_rule_set(
        state,
        [{"actor": "Player_1", "data": {"move": {"plane": "A", "roll": 5}}}],
        "builtin.board.flight_chess_lite",
        {"track_size": 52},
    )
    assert blocked["accepted"] is False
    assert "6" in blocked["reason"]

    takeoff = _apply_rule_set(
        state,
        [{"actor": "Player_1", "data": {"move": {"plane": "A", "roll": 6}}}],
        "builtin.board.flight_chess_lite",
        {"track_size": 52},
    )
    assert takeoff["accepted"] is True
    assert state.get_attr("Player_1", "planes")["A"] == 0

    hit = _apply_rule_set(
        state,
        [{"actor": "Player_1", "data": {"move": {"plane": "A", "roll": 4}}}],
        "builtin.board.flight_chess_lite",
        {"track_size": 52},
    )
    assert hit["hit_planes"] == [{"actor": "Player_2", "plane": "A"}]
    assert state.get_attr("Player_2", "planes")["A"] == "base"


def test_xiangqi_rule_set_moves_piece_and_detects_general_capture():
    """象棋 rule_set 应移动棋子，并在吃到将/帅时写入赢家。"""
    state = _state()
    state.register_entity("GAME", {"xiangqi_board": {"0,4": "B_general", "1,4": "R_chariot"}})
    state.register_entity("Player_1", {"role": "red"})

    result = _apply_rule_set(
        state,
        [{"actor": "Player_1", "data": {"move": {"from": [1, 4], "to": [0, 4]}}}],
        "builtin.board.xiangqi_lite",
    )

    assert result["accepted"] is True
    assert result["captured"] == "B_general"
    assert state.get_attr("GAME", "winner") == "Player_1"
    assert state.get_attr("GAME", "xiangqi_board")["0,4"] == "R_chariot"


def test_go_rule_set_places_stone_and_captures_group_without_liberty():
    """围棋 rule_set 应落子，并提掉无气棋块。"""
    state = _state()
    state.register_entity("GAME", {"go_board": {"1,1": "W", "0,1": "B", "1,0": "B", "2,1": "B"}})
    state.register_entity("Player_1", {"role": "black"})

    result = _apply_rule_set(
        state,
        [{"actor": "Player_1", "data": {"move": {"position": [1, 2]}}}],
        "builtin.board.go_lite",
    )

    assert result["accepted"] is True
    assert result["captured"] == [[1, 1]]
    assert "1,1" not in state.get_attr("GAME", "go_board")


def test_checkers_rule_set_moves_and_captures_mid_piece():
    """跳棋 rule_set 应支持斜向跳吃并移除中间棋子。"""
    state = _state()
    state.register_entity("GAME", {"checkers_board": {"5,0": "D", "4,1": "L"}})
    state.register_entity("Player_1", {"role": "dark"})

    result = _apply_rule_set(
        state,
        [{"actor": "Player_1", "data": {"move": {"from": [5, 0], "to": [3, 2]}}}],
        "builtin.board.checkers_lite",
    )

    assert result["accepted"] is True
    assert result["captured"] == "L"
    board = state.get_attr("GAME", "checkers_board")
    assert "4,1" not in board
    assert board["3,2"] == "D"


def test_card_event_rule_set_draws_event_and_scores_player():
    """牌堆事件 rule_set 应抽事件牌并给玩家加分。"""
    state = _state()
    state.register_entity("GAME", {"event_deck": ["storm", "auction"]})
    state.register_entity("Player_1", {"role": "player", "score": 0})

    draw = _apply_rule_set(
        state,
        [{"actor": "Player_1", "data": {"card_action": {"type": "draw"}}}],
        "builtin.cards.card_event_party_lite",
    )
    assert draw["event"] == "storm"
    assert state.get_attr("GAME", "current_event") == "storm"

    score = _apply_rule_set(
        state,
        [{"actor": "Player_1", "data": {"card_action": {"type": "score", "target": "Player_1", "points": 3}}}],
        "builtin.cards.card_event_party_lite",
    )
    assert score["score"] == 3
    assert state.get_attr("Player_1", "score") == 3


def test_asset_trading_rule_set_settles_cash_and_assets():
    """资产交易 rule_set 应在接受交易时更新现金和资产。"""
    state = _state()
    state.register_entity("GAME", {})
    state.register_entity("Player_1", {"role": "tycoon", "cash": 100, "assets": ["farm"], "alive": True})
    state.register_entity("Player_2", {"role": "tycoon", "cash": 80, "assets": [], "alive": True})

    result = _apply_rule_set(
        state,
        [{
            "actor": "Player_2",
            "data": {
                "trade": {"seller": "Player_1", "buyer": "Player_2", "asset": "farm", "price": 30},
                "accept": True,
            },
        }],
        "builtin.economy.asset_trading_lite",
    )

    assert result["settled"] is True
    assert state.get_attr("Player_1", "cash") == 130
    assert state.get_attr("Player_2", "cash") == 50
    assert state.get_attr("Player_1", "assets") == []
    assert state.get_attr("Player_2", "assets") == ["farm"]


def test_dice_map_adventure_rule_set_moves_and_reaches_treasure():
    """骰子地图冒险 rule_set 应移动节点、消耗补给，并在到达宝藏时写胜利。"""
    state = _state()
    state.register_entity("GAME", {})
    state.register_entity("Player_1", {"role": "player", "node": 8, "supplies": 2})

    result = _apply_rule_set(
        state,
        [{"actor": "Player_1", "data": {"move": {"roll": 3}}}],
        "builtin.story.dice_map_adventure_lite",
        {"treasure_node": 11},
    )

    assert result["node"] == 11
    assert result["supplies"] == 1
    assert result["reached_treasure"] is True
    assert state.get_attr("GAME", "winner") == "party"


def test_d20_story_rule_set_records_success_progress():
    """DND/跑团类 story rule_set 应记录 d20 成功进度和记忆。"""
    state = _state()
    state.register_entity("GAME", {})
    state.register_entity("Player_1", {"role": "player"})

    result = _apply_rule_set(
        state,
        [{"actor": "Player_1", "data": {"action": "破门救人", "roll": 18}}],
        "builtin.story.dnd_fixed_adventure",
        {"dc": 12},
    )

    assert result["check_passed"] is True
    assert state.get_attr("GAME", "dnd_fixed_adventure_successes") == 1
    assert state.get_attr("GAME", "story_memory")[0]["action"] == "破门救人"


def test_coc_story_rule_set_records_clues_and_sanity_loss():
    """COC rule_set 应记录线索，并在理智检定失败时扣理智。"""
    state = _state()
    state.register_entity("GAME", {})
    state.register_entity("Player_1", {"role": "player", "sanity": 60})

    clue = _apply_rule_set(
        state,
        [{"actor": "Player_1", "data": {"location": "灯塔", "method": "检查符号"}}],
        "builtin.story.coc_fixed_mystery",
    )
    assert clue["clue_count"] == 1
    assert state.get_attr("GAME", "clues")[0]["location"] == "灯塔"

    sanity = _apply_rule_set(
        state,
        [{"actor": "Player_1", "data": {"roll": 95, "reaction": "恐惧"}}],
        "builtin.story.coc_fixed_mystery",
    )
    assert sanity["check_passed"] is False
    assert state.get_attr("Player_1", "sanity") == 55


def test_text_adventure_rule_set_records_log_and_escape_win():
    """文字冒险 rule_set 应记录行动，并在逃脱关键词出现时写入胜利。"""
    state = _state()
    state.register_entity("GAME", {})
    state.register_entity("Player_1", {"role": "player"})

    result = _apply_rule_set(
        state,
        [{"actor": "Player_1", "data": {"target": "生锈钥匙", "action": "用钥匙开锁逃出房间"}}],
        "builtin.story.text_adventure_lite",
    )

    assert result["escaped"] is True
    assert state.get_attr("GAME", "winner") == "Player_1"
    assert state.get_attr("GAME", "adventure_log")[0]["target"] == "生锈钥匙"


def test_uno_rule_set_applies_play_draw_and_function_card_effects():
    """UNO rule_set 应校验手牌、匹配顶牌，并写入功能牌效果。"""
    state = _state()
    state.register_entity("GAME", {
        "uno_top_card": "red-5",
        "uno_draw_pile": ["green-9"],
        "uno_direction": 1,
    })
    state.register_entity("Player_1", {"role": "player", "hand": ["red-reverse", "blue-7"]})
    state.register_entity("Player_2", {"role": "player", "hand": []})

    bad = _apply_rule_set(
        state,
        [{"actor": "Player_1", "data": {"card_action": {"type": "play", "card": "blue-7"}}}],
        "builtin.cards.uno_lite",
    )
    assert bad["accepted"] is False
    assert "匹配" in bad["reason"]

    played = _apply_rule_set(
        state,
        [{"actor": "Player_1", "data": {"card_action": {"type": "play", "card": "red-reverse"}}}],
        "builtin.cards.uno_lite",
    )
    assert played["accepted"] is True
    assert played["effect"] == "reverse"
    assert state.get_attr("GAME", "uno_direction") == -1
    assert state.get_attr("GAME", "uno_top_card") == "red-reverse"

    drawn = _apply_rule_set(
        state,
        [{"actor": "Player_2", "data": {"card_action": {"type": "draw"}}}],
        "builtin.cards.uno_lite",
    )
    assert drawn["drawn"] == "green-9"
    assert state.get_attr("Player_2", "hand") == ["green-9"]


def test_monopoly_rule_set_handles_purchase_rent_and_bankruptcy():
    """大富翁 rule_set 应处理购买、收租和现金为负后的破产。"""
    state = _state()
    state.register_entity("GAME", {"monopoly_owners": {}})
    state.register_entity("Player_1", {"role": "tycoon", "cash": 500, "position": 0, "alive": True})
    state.register_entity("Player_2", {"role": "tycoon", "cash": 10, "position": 0, "alive": True})
    config = {"board_size": 20, "properties": {"3": {"price": 100, "rent": 40}}}

    bought = _apply_rule_set(
        state,
        [{"actor": "Player_1", "data": {"move": {"steps": 3, "buy": True}}}],
        "builtin.board.monopoly_lite",
        config,
    )
    assert bought["bought"] is True
    assert state.get_attr("GAME", "monopoly_owners")["3"] == "Player_1"
    assert state.get_attr("Player_1", "cash") == 400

    rent = _apply_rule_set(
        state,
        [{"actor": "Player_2", "data": {"move": {"steps": 3}}}],
        "builtin.board.monopoly_lite",
        config,
    )
    assert rent["rent_paid"] == 40
    assert rent["bankrupt"] is True
    assert state.get_attr("Player_2", "alive") is False
    assert state.get_attr("Player_1", "cash") == 440


def test_texas_holdem_rule_set_compares_showdown_hands():
    """德州扑克 rule_set 应比较最佳五张牌并写入赢家。"""
    state = _state()
    state.register_entity("GAME", {"community_cards": ["AH", "KH", "QH", "2C", "3D"]})
    state.register_entity("Player_1", {"role": "poker_player", "chips": 100})
    state.register_entity("Player_2", {"role": "poker_player", "chips": 100})

    result = _apply_rule_set(
        state,
        [
            {"actor": "Player_1", "data": {"cards": ["JH", "TH"]}},
            {"actor": "Player_2", "data": {"cards": ["AS", "AD"]}},
        ],
        "builtin.cards.texas_holdem_party_lite",
    )

    assert result["showdown"] is True
    assert result["winner"] == "Player_1"
    assert result["best_hand"]["category"] == "straight_flush"
    assert state.get_attr("GAME", "winner") == "Player_1"
