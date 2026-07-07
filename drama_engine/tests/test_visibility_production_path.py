"""可见性生产路径回归测试（对应 docs/plan/visibility_hardening_plan.md）。

区别于 test_visibility_e2e.py：后者直接调 `instance.assign()`（门面路径），
本测试走**生产路径** `SessionRegistry.assign_session()`——这正是 Web service
(`service/server/app.py`) 实际使用的发牌入口。审查（architecture_review_2026-07.md H1）
发现生产路径绕过 GameInstance.assign，导致 KnowledgeFirewall 从不按脚本重建。

四个用例守卫三处缺口 + 一处既有能力：
  1. 生产发牌路径隔离 —— firewall 必须按脚本 secret_attrs 重建（守卫缺口1）
  2. inside-agent 回退隔离 —— 借用执行体不得看到他人秘密（守卫缺口2）
  3. purpose 不可升级 —— 玩家身份无论 purpose 一律受限（守卫缺口3）
  4. disclosure 正确性 —— 披露只对被披露者可见（守卫既有能力不回归）
"""

from __future__ import annotations

import pytest

from drama_engine.core.game_instance.factory import GameInstanceFactory
from drama_engine.core.session.registry import SessionRegistry

# 该脚本声明了 visibility.secret_attrs=[role]，是验证 firewall 重建的最小载体。
_SCRIPT = "drama_engine/scripts/interactive_session/deduction/who_is_undercover_visibility.yaml"
_SEATS = [f"Player_{i}" for i in range(1, 7)]


async def _assign_via_production_path() -> object:
    """走生产路径创建并发牌：SessionRegistry.create_session + assign_session。

    返回该 session 对应的 GameInstance（经 registry 生命周期发牌后）。
    """
    registry = SessionRegistry(store=None, load_existing=False)
    runtime = await registry.create_session(
        game_id="who_is_undercover_visibility",
        script_path=_SCRIPT,
        seat_ids=list(_SEATS),
        params={"dry_run": True, "use_runner": True},
    )
    session_id = runtime.session.session_id
    # 生产发牌入口：service 层实际调用的就是这个，而非 instance.assign()。
    await registry.assign_session(session_id)
    # 取该 session 的 GameInstance 做投影断言（与 app.py 一致的缓存约定）。
    runtime = await registry.get_session(session_id)
    instance = getattr(runtime, "_game_instance", None)
    if instance is None:
        instance = GameInstanceFactory.wrap(runtime)
    return instance


@pytest.mark.asyncio
async def test_production_assign_rebuilds_firewall() -> None:
    """缺口1：生产路径发牌后，firewall 必须按脚本 secret_attrs 遮蔽他人 role。

    修复前：registry 直连 runtime.assign()，firewall 停在默认空实例，
            他人 role 全部可见 —— 本断言会失败，暴露缺口。
    修复后：registry 走 GameInstance.assign()，firewall 按 [role] 重建。
    """
    instance = await _assign_via_production_path()
    view = instance.project_context("agent:Player_1", "prompt")
    assert view["audience_kind"] == "restricted"
    # 自己的 role 可见
    assert view["self"].get("role") == "civilian"
    # 关键：他人的 role 必须被遮蔽（缺口1 的核心断言）
    assert "role" not in view["others"]["Player_6"], (
        "生产路径发牌后他人 role 仍可见——firewall 未按脚本重建（H1 缺口1）"
    )


@pytest.mark.asyncio
async def test_player_purpose_cannot_escalate_to_full_state() -> None:
    """缺口3：玩家身份无论 purpose 传什么，都只拿受限视图，不能升级为全量。

    修复前：purpose in {referee,recap} 无条件返回全量，玩家传 referee 即开天眼。
    修复后：授权只看身份，player:* 一律受限。
    """
    instance = await _assign_via_production_path()
    # 玩家伪装 referee purpose 尝试拿全量
    view = instance.project_context("player:Player_1", "referee")
    assert view.get("audience_kind") == "restricted", (
        "玩家用 referee purpose 拿到了非受限视图——授权被 purpose 升级（H1 缺口3）"
    )
    assert "state" not in view, "受限视图不应包含全量 state 快照"
    # recap 同理
    view_recap = instance.project_context("player:Player_1", "recap")
    assert view_recap.get("audience_kind") == "restricted"


@pytest.mark.asyncio
async def test_host_still_privileged() -> None:
    """收紧授权不能误伤 host：host 身份仍拿全量 state（含所有人 role）。"""
    instance = await _assign_via_production_path()
    view = instance.project_context("host", "view")
    assert "state" in view, "host 应始终拿到全量 state"
    assert view["state"]["Player_6"]["role"] == "undercover"


@pytest.mark.asyncio
async def test_disclosure_only_visible_to_disclosed_actor() -> None:
    """既有能力守卫：披露事实只对被披露者可见，他人 disclosed 为空。"""
    instance = await _assign_via_production_path()
    ledger = instance._current_disclosure_ledger()
    assert ledger is not None
    ledger.record("Player_1", "GAME.last_inspection_result", {"target": "Player_6", "role": "undercover"})

    seen = instance.project_context("agent:Player_1", "prompt")
    assert seen["disclosed"]["GAME.last_inspection_result"]["role"] == "undercover"
    other = instance.project_context("agent:Player_2", "prompt")
    assert other["disclosed"] == {}, "未被披露的玩家不应看到披露事实"
