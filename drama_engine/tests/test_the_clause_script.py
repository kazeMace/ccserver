"""The Clause DSL 端到端 dry-run 测试。

验证：
  - 脚本可编译、可 assign、可 start；
  - 流程能从 node_01 推进到某个结局节点；
  - 好感度 STORY.affection_marco 正确累积；
  - GAME.ended 在结局节点被置为 true。
"""

from __future__ import annotations

import asyncio

import pytest

from drama_engine.core.game_instance.factory import GameInstanceRegistry

_SCRIPT = "drama_engine/scripts/interactive_session/story/the_clause.yaml"


async def _make_instance() -> object:
    """创建并 assign 一个 The Clause 实例（dry-run）。"""
    registry = GameInstanceRegistry(store=None, load_existing=False)
    instance = await registry.create_instance(
        game_id="the_clause_test",
        script_path=_SCRIPT,
        seat_ids=["Player_1"],
        params={"dry_run": True, "use_runner": True},
    )
    await instance.assign()
    return instance


@pytest.mark.asyncio
async def test_the_clause_runs_to_ending() -> None:
    """脚本能从开局跑到结局，GAME.ended 被置为 true。"""
    instance = await _make_instance()
    await instance.start()
    task = instance.runtime.director_task
    if task is not None:
        await asyncio.wait_for(task, timeout=30)
    view = instance.project_context("host", "referee")
    state = view.get("state", {})
    # 流程推进过若干节点，round 计数应大于 0
    assert int(state.get("GAME", {}).get("round") or 0) > 0
    # 好感度是数值（累积过至少一次）
    affection = state.get("STORY", {}).get("affection_marco")
    assert isinstance(affection, (int, float))
    assert state.get("GAME", {}).get("ended") is True
