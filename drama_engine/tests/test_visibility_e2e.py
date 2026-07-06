"""模块5：KnowledgeFirewall + DisclosureLedger 全链路端到端测试。

通过 GameInstance 门面验证：
  (a) 声明 visibility.secret_attrs=[role] 后，firewall 按声明遮蔽他人 role；
  (b) 披露事实后，firewall 投影把它并入该 actor 的 disclosed；
  (c) checkpoint → 再披露 → rollback 后披露被截断。
"""

from __future__ import annotations

import pytest

from drama_engine.core.game_instance.factory import GameInstanceRegistry

_SCRIPT = "drama_engine/scripts/interactive_session/deduction/who_is_undercover_visibility.yaml"


async def _make_instance() -> object:
    """创建并 assign 一个声明了 visibility 的谁是卧底实例。"""
    registry = GameInstanceRegistry(store=None, load_existing=False)
    instance = await registry.create_instance(
        game_id="who_is_undercover_visibility",
        script_path=_SCRIPT,
        seat_ids=[f"Player_{i}" for i in range(1, 7)],
        params={"dry_run": True, "use_runner": True},
    )
    await instance.assign()
    return instance


@pytest.mark.asyncio
async def test_firewall_built_from_declared_visibility() -> None:
    """firewall 按脚本 visibility 声明构建：他人 role 被遮蔽，自己 role 可见。"""
    instance = await _make_instance()
    view = instance.project_context("agent:Player_1", "prompt")
    assert view["audience_kind"] == "restricted"
    # 自己的 role 可见
    assert view["self"].get("role") == "civilian"
    # 他人的 role 被遮蔽，但 alive 仍可见
    assert "role" not in view["others"]["Player_6"]
    assert view["others"]["Player_6"].get("alive") is True


@pytest.mark.asyncio
async def test_privileged_host_sees_full_state() -> None:
    """host + referee 授权拿到完整 state（含所有人 role）。"""
    instance = await _make_instance()
    view = instance.project_context("host", "referee")
    assert "state" in view
    assert view["state"]["Player_6"]["role"] == "undercover"


@pytest.mark.asyncio
async def test_disclosure_merged_into_projection() -> None:
    """披露事实后，firewall 投影把它并入该 actor 的 disclosed。"""
    instance = await _make_instance()
    ledger = instance._current_disclosure_ledger()
    assert ledger is not None
    # 模拟一次「验人」：把 Player_6 的身份披露给 Player_1
    ledger.record("Player_1", "GAME.last_inspection_result", {"target": "Player_6", "role": "undercover"})

    view = instance.project_context("agent:Player_1", "prompt")
    assert view["disclosed"]["GAME.last_inspection_result"]["role"] == "undercover"
    # 但直接投影里他人 role 仍被遮蔽（披露是独立通道）
    assert "role" not in view["others"]["Player_6"]
    # 其他玩家没有被披露，disclosed 为空
    other_view = instance.project_context("agent:Player_2", "prompt")
    assert other_view["disclosed"] == {}


@pytest.mark.asyncio
async def test_disclosure_truncated_on_rollback() -> None:
    """checkpoint → 再披露 → rollback 后，披露被截断丢弃。"""
    instance = await _make_instance()
    ledger = instance._current_disclosure_ledger()

    # 建 checkpoint（此时无披露）
    summary = instance.checkpoint("before_inspection")
    # checkpoint 之后披露一条
    ledger.record("Player_1", "GAME.last_inspection_result", {"target": "Player_6", "role": "undercover"})
    assert instance.project_context("agent:Player_1", "prompt")["disclosed"] != {}

    # 回滚到 checkpoint：披露应消失
    await instance.rollback_to(summary["checkpoint_id"])
    ledger_after = instance._current_disclosure_ledger()
    assert ledger_after.facts_for("Player_1") == {}
    assert instance.project_context("agent:Player_1", "prompt")["disclosed"] == {}
