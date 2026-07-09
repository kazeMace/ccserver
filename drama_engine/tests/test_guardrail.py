"""模块4：OOC 内容守卫 GuardRail 测试（策略模式）。

用假 executor 模拟 LLM 判定，验证：
  - enabled=false 完全旁路；
  - 合规发言放行；
  - 四种违规策略各自行为（block/rewrite/soft_warn/pass_with_flag）。
"""

from __future__ import annotations

import pytest

from drama_engine.core.moderation.guardrail import GuardRail, build_guardrail
from drama_engine.core.moderation.models import GuardRailSpec
from drama_engine.core.moderation.strategies import build_strategy


class _FakeEvaluator:
    """假条件求值器：evaluate_async 返回预设 bool（True=合规）。"""

    def __init__(self, passed: bool) -> None:
        self._passed = passed
        self.calls = 0

    async def evaluate_async(self, cond, state, actor=None, extra=None):
        self.calls += 1
        return self._passed


class _FakeCtx:
    """最小 ctx：只提供 GuardRail.check 需要的字段。"""

    def __init__(self, evaluator) -> None:
        self.condition_evaluator = evaluator
        self.state = None
        self.session_metadata = {}

    def condition_extra(self, **items):
        return {}


def _spec(on_violation: str, enabled: bool = True) -> GuardRailSpec:
    return GuardRailSpec(
        enabled=enabled,
        checks=["in_character", "on_topic"],
        on_violation=on_violation,
        executor={"kind": "llm", "provider": "inside"},
    )


@pytest.mark.asyncio
async def test_disabled_bypasses() -> None:
    """未启用时完全放行，不调用 executor。"""
    evaluator = _FakeEvaluator(passed=False)
    guard = GuardRail(_spec("block", enabled=False))
    outcome = await guard.check(_FakeCtx(evaluator), {"actor": "P1", "text": "任意"})
    assert outcome.allow is True
    assert evaluator.calls == 0  # 旁路，未判定


@pytest.mark.asyncio
async def test_compliant_speech_passes() -> None:
    """判定合规（passed=True）时放行且不打标。"""
    guard = GuardRail(_spec("block"))
    outcome = await guard.check(_FakeCtx(_FakeEvaluator(passed=True)), {"actor": "P1", "text": "在场景内发言"})
    assert outcome.allow is True
    assert outcome.flagged is False


@pytest.mark.asyncio
async def test_block_strategy_intercepts() -> None:
    """违规 + block：拦截不放行。"""
    guard = GuardRail(_spec("block"))
    outcome = await guard.check(_FakeCtx(_FakeEvaluator(passed=False)), {"actor": "P1", "text": "现实世界闲聊"})
    assert outcome.allow is False
    assert outcome.flagged is True


@pytest.mark.asyncio
async def test_soft_warn_strategy_passes_flagged() -> None:
    """违规 + soft_warn：放行但打标。"""
    guard = GuardRail(_spec("soft_warn"))
    outcome = await guard.check(_FakeCtx(_FakeEvaluator(passed=False)), {"actor": "P1", "text": "出圈发言"})
    assert outcome.allow is True
    assert outcome.flagged is True


@pytest.mark.asyncio
async def test_pass_with_flag_strategy() -> None:
    """违规 + pass_with_flag：放行并记标记。"""
    guard = GuardRail(_spec("pass_with_flag"))
    outcome = await guard.check(_FakeCtx(_FakeEvaluator(passed=False)), {"actor": "P1", "text": "出圈发言"})
    assert outcome.allow is True
    assert outcome.flagged is True


@pytest.mark.asyncio
async def test_rewrite_strategy_without_client_degrades() -> None:
    """违规 + rewrite 但无改写 client：降级为打标放行（不中断游戏）。"""
    guard = GuardRail(_spec("rewrite"))
    # _FakeCtx 无 inside client，改写器不可用 → 降级放行
    outcome = await guard.check(_FakeCtx(_FakeEvaluator(passed=False)), {"actor": "P1", "text": "出圈发言"})
    assert outcome.allow is True
    assert outcome.flagged is True


def test_build_guardrail_returns_none_when_disabled() -> None:
    """build_guardrail：未启用返回 None。"""
    assert build_guardrail(GuardRailSpec(enabled=False)) is None
    assert build_guardrail(None) is None
    assert build_guardrail(_spec("block")) is not None


def test_build_strategy_unknown_raises() -> None:
    """未知策略名断言失败。"""
    with pytest.raises(AssertionError):
        build_strategy("nonexistent")


def test_guardrail_spec_rejects_bad_on_violation() -> None:
    """GuardRailSpec 拒绝非法 on_violation。"""
    with pytest.raises(AssertionError):
        GuardRailSpec(on_violation="explode")
