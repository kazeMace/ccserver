"""GameInstance 门面测试。

验证 service 层可以只通过 GameInstance 完成创建、发牌、启动、取视图、读 timeline，
以及 dry-run 下 interactive_session 流程能跑到结束。
"""

from __future__ import annotations

import asyncio

import pytest

from drama_engine.core.game_instance.factory import GameInstanceRegistry
from drama_engine.core.game_instance.instance import GameInstance

_SCRIPT = "drama_engine/scripts/interactive_session/story/text_adventure_interactive.yaml"


@pytest.mark.asyncio
async def test_game_instance_create_and_views() -> None:
    """通过 registry 创建 GameInstance 并取各视图。"""
    registry = GameInstanceRegistry(store=None, load_existing=False)
    instance = await registry.create_instance(
        game_id="story",
        script_path=_SCRIPT,
        seat_ids=["Player_1"],
        params={"dry_run": True, "use_runner": True},
    )
    assert isinstance(instance, GameInstance)
    assert instance.status == "lobby"

    # 视图接口可用
    assert isinstance(instance.host_view(), dict)
    assert isinstance(instance.public_view(), dict)
    assert isinstance(instance.player_view("Player_1"), dict)

    # timeline 接口可用
    assert isinstance(instance.timeline("host"), list)


@pytest.mark.asyncio
async def test_game_instance_join_and_leave_player() -> None:
    """join/leave 应更新 seat 的 claimed_by。"""
    registry = GameInstanceRegistry(store=None, load_existing=False)
    instance = await registry.create_instance(
        game_id="story",
        script_path=_SCRIPT,
        seat_ids=["Player_1", "Player_2"],
        params={"use_runner": False},
    )
    instance.join_player("Player_1", "user-a")
    assert instance.runtime.session.seats["Player_1"].claimed_by == "user-a"
    instance.leave_player("Player_1", "user-a")
    assert instance.runtime.session.seats["Player_1"].claimed_by is None


@pytest.mark.asyncio
async def test_game_instance_lifecycle_dry_run_reaches_end() -> None:
    """dry-run 下经 GameInstance 走 assign→start，最终进入 ended/failed。"""
    registry = GameInstanceRegistry(store=None, load_existing=False)
    instance = await registry.create_instance(
        game_id="story",
        script_path=_SCRIPT,
        seat_ids=["Player_1"],
        params={"dry_run": True, "use_runner": True},
    )
    await instance.assign()
    assert instance.status == "assigned"
    await instance.start()

    # 等待后台 flow task 结束
    task = instance.runtime.director_task
    if task is not None:
        await asyncio.wait_for(task, timeout=30)
    assert instance.status in {"ended", "failed"}


@pytest.mark.asyncio
async def test_control_and_rollback_apis_are_explicitly_unimplemented() -> None:
    """未接入的 control/rollback 接口应显式抛错，而不是静默返回。"""
    registry = GameInstanceRegistry(store=None, load_existing=False)
    instance = await registry.create_instance(
        game_id="story",
        script_path=_SCRIPT,
        seat_ids=["Player_1"],
        params={"use_runner": False},
    )
    with pytest.raises(NotImplementedError):
        instance.submit_control_action("writer", {})
    with pytest.raises(NotImplementedError):
        instance.rollback_points()
