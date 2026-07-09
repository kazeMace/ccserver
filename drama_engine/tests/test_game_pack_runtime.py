"""GamePack 接入 interactive_session runtime 的端到端测试。

验证 DSL 顶层 game_pack 引用能在 runner.assign 阶段把机制注册进 plugin registry，
并把默认 config 合并进 GAME 状态；机制随后可通过 effect/condition 被调用。
"""

from __future__ import annotations

import pytest

from drama_engine.core.engine import SetAttr, StateWriter
from drama_engine.core.components import EffectExecutor  # noqa: F401 - 先加载组件，避免循环导入
from drama_engine.core.plugins import EffectContext
from drama_engine.core.game_instance.factory import GameInstanceRegistry

_GOMOKU = "drama_engine/scripts/interactive_session/board/gomoku.yaml"


@pytest.mark.asyncio
async def test_gomoku_game_pack_installs_board_mechanics() -> None:
    """assign 后：board 机制已注册、config 已写入 GAME、connect_n 可判胜。"""
    registry = GameInstanceRegistry(store=None, load_existing=False)
    instance = await registry.create_instance(
        game_id="gomoku",
        script_path=_GOMOKU,
        seat_ids=["Player_1", "Player_2"],
        params={"dry_run": True, "use_runner": True},
    )
    await instance.assign()

    runner = instance.runtime.runner
    ctx = runner._ctx
    assert ctx is not None
    # 机制已注册进本局 plugin registry
    assert ctx.plugin_registry.has_effect("board_place")
    assert ctx.plugin_registry.has_condition("board.connect_n")
    # game_pack config 已写入 GAME
    assert ctx.state.get_attr("GAME", "board_size") == 15
    assert ctx.state.get_attr("GAME", "win_length") == 5

    # 用 board_place 机制在同一 state 上连下 5 子，connect_n 应判黑胜
    StateWriter(ctx.state).apply(SetAttr("Player_1", "role", "black"))
    for col in range(5):
        effect_ctx = EffectContext(
            state=ctx.state,
            writer=ctx.writer,
            actor="Player_1",
            responses=[],
            scene_name="black_move",
            extra={},
        )
        ctx.plugin_registry.execute_effect(
            {"type": "board_place", "position": [0, col], "piece": "black"},
            effect_ctx,
        )
    assert ctx.plugin_registry.evaluate_condition(
        "board.connect_n", {"input": {"n": 5}}, {"state": ctx.state}
    ) is True


@pytest.mark.asyncio
async def test_script_without_game_pack_has_no_board_mechanics() -> None:
    """纯剧情脚本不引入机制：零关联。"""
    registry = GameInstanceRegistry(store=None, load_existing=False)
    instance = await registry.create_instance(
        game_id="story",
        script_path="drama_engine/scripts/interactive_session/story/text_adventure_interactive.yaml",
        seat_ids=["Player_1"],
        params={"dry_run": True, "use_runner": True},
    )
    await instance.assign()
    ctx = instance.runtime.runner._ctx
    assert ctx is not None
    assert ctx.plugin_registry.has_effect("board_place") is False


@pytest.mark.asyncio
async def test_rpg_installs_dice_inventory_stats_together() -> None:
    """RPG 样例用列表形式引入 dice+inventory+stats 三个机制集合，均应安装并跑通。"""
    registry = GameInstanceRegistry(store=None, load_existing=False)
    instance = await registry.create_instance(
        game_id="rpg",
        script_path="drama_engine/scripts/interactive_session/rpg/dungeon_delve.yaml",
        seat_ids=["Hero"],
        params={"dry_run": True, "use_runner": True},
    )
    await instance.assign()
    ctx = instance.runtime.runner._ctx
    # 三个机制集合都装上了
    assert ctx.plugin_registry.has_effect("roll_dice")
    assert ctx.plugin_registry.has_effect("grant_item")
    assert ctx.plugin_registry.has_effect("adjust_attr")
    # 具名骰子定义已写入 GAME
    defs = ctx.state.get_attr("GAME", "dice_defs")
    assert isinstance(defs, dict) and "d20" in defs and "encounter" in defs
    # 角色面板初始值来自 DSL
    assert ctx.state.get_attr("Hero", "hp") == 30
    assert ctx.state.get_attr("Hero", "inventory_healing_herb") == 2
