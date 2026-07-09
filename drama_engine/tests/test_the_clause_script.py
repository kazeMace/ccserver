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


async def _make_human_instance() -> object:
    """创建一个真人 Player_1 的 The Clause 实例，用于验证前端 pending。"""
    registry = GameInstanceRegistry(store=None, load_existing=False)
    instance = await registry.create_instance(
        game_id="the_clause_human",
        script_path=_SCRIPT,
        seat_ids=["Player_1"],
        human_seat_ids={"Player_1"},
        params={"dry_run": False, "use_runner": True},
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


@pytest.mark.asyncio
async def test_the_clause_human_inbox_exposes_choice_and_text_input() -> None:
    """真人玩家启动后必须在 inbox 收到选项 + 自由输入，而不是由 dry-run 自动选择。"""
    instance = await _make_human_instance()
    await instance.start()
    task = instance.runtime.director_task
    assert task is not None

    pending = None
    for _ in range(20):
        inbox = instance.inbox("player:Player_1", after=0)
        pending = inbox.get("pending")
        if pending is not None:
            break
        await asyncio.sleep(0.05)

    assert pending is not None
    assert pending["primitive"] == "choice_or_text"
    assert pending["prompt"] == "请选择一个选项。"
    assert pending["free_input"] is not None
    assert [option["id"] for option in pending["options"]] == ["get_in_car", "ignore_walk"]
    assert [option["text"] for option in pending["options"]] == ["上车", "不理他，继续往前走"]

    ack = await instance.reply("player:Player_1", {
        "request_id": pending["request_id"],
        "choice_id": "get_in_car",
        "text": "我会上车，但只给你十分钟解释。",
    })
    assert ack["accepted"] is True

    # 提交选项后，剧情推进到 node_05_02a，该场景有视频 + 新的选项
    # 等待下一个 pending 出现（证明流程已推进到第二个场景）
    next_pending = None
    for _ in range(40):
        inbox = instance.inbox("player:Player_1", after=0)
        next_pending = inbox.get("pending")
        if next_pending is not None and next_pending.get("request_id") != pending["request_id"]:
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("提交选项后剧情没有推进到下一段")

    # 第二个场景（s_05_offer_a）也应该是 choice_or_text，选项为 "Next day at the office"
    assert next_pending["primitive"] == "choice_or_text"
    assert any(opt["id"] == "next_day_at_the_office" for opt in next_pending["options"])

    if not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
