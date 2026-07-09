"""模块4：OOC 内容守卫 GuardRail 测试。

用假 ExecutorRegistry 模拟 LLM 判定，验证：
  - enabled=false 完全旁路；
  - 合规发言放行；
  - 四种违规策略各自行为（block/rewrite/soft_warn/pass_with_flag）。
"""

from __future__ import annotations

import pytest

from drama_engine.core.moderation.guardrail import LLMGuardRail, build_guardrail
from drama_engine.core.moderation.models import GuardRailSpec
from drama_engine.core.moderation.strategies import build_strategy
from drama_engine.core.executor.base import ExecutorResponse


class _FakeExecutorRegistry:
    """假 ExecutorRegistry：返回预设的判定结果。"""

    def __init__(self, result: bool, confidence: float = 1.0) -> None:
        self._result = result
        self._confidence = confidence
        self.calls = 0

    async def execute(self, executor_name: str, request):
        self.calls += 1
        return ExecutorResponse(
            success=True,
            data={"result": self._result, "confidence": self._confidence},
        )


class _FakeCtx:
    """最小 ctx：只提供 GuardRail.check 需要的字段。"""

    def __init__(self, executor_registry) -> None:
        self.executor_registry = executor_registry
        self.state = None
        self.session_metadata = {}


def _spec(on_violation: str, enabled: bool = True) -> GuardRailSpec:
    return GuardRailSpec(
        enabled=enabled,
        checks=["in_character", "on_topic"],
        on_violation=on_violation,
        executor="llm",
    )


def _build(on_violation: str, passed: bool, enabled: bool = True, confidence: float = 1.0) -> tuple:
    """构建 guard + fake ctx 组合。"""
    spec = _spec(on_violation, enabled=enabled)
    guard = build_guardrail(spec)
    registry = _FakeExecutorRegistry(result=passed, confidence=confidence)
    ctx = _FakeCtx(registry)
    return guard, ctx, registry


@pytest.mark.asyncio
async def test_disabled_bypasses() -> None:
    """未启用时完全放行，不调用 executor。"""
    spec = _spec("block", enabled=False)
    guard = build_guardrail(spec)
    assert guard is None


@pytest.mark.asyncio
async def test_compliant_speech_passes() -> None:
    """判定合规（result=True）时放行且不打标。"""
    guard, ctx, registry = _build("block", passed=True)
    outcome = await guard.check(ctx, {"actor": "P1", "text": "在场景内发言"})
    assert outcome.allow is True
    assert outcome.flagged is False
    assert registry.calls == 1


@pytest.mark.asyncio
async def test_block_strategy_intercepts() -> None:
    """违规 + block：拦截不放行。"""
    guard, ctx, _ = _build("block", passed=False)
    outcome = await guard.check(ctx, {"actor": "P1", "text": "现实世界闲聊"})
    assert outcome.allow is False
    assert outcome.flagged is True


@pytest.mark.asyncio
async def test_soft_warn_strategy_passes_flagged() -> None:
    """违规 + soft_warn：放行但打标。"""
    guard, ctx, _ = _build("soft_warn", passed=False)
    outcome = await guard.check(ctx, {"actor": "P1", "text": "出圈发言"})
    assert outcome.allow is True
    assert outcome.flagged is True


@pytest.mark.asyncio
async def test_pass_with_flag_strategy() -> None:
    """违规 + pass_with_flag：放行并记标记。"""
    guard, ctx, _ = _build("pass_with_flag", passed=False)
    outcome = await guard.check(ctx, {"actor": "P1", "text": "出圈发言"})
    assert outcome.allow is True
    assert outcome.flagged is True


@pytest.mark.asyncio
async def test_rewrite_strategy_without_client_degrades() -> None:
    """违规 + rewrite：通过 executor 改写；改写返回空时降级打标放行。"""
    guard, ctx, _ = _build("rewrite", passed=False)
    outcome = await guard.check(ctx, {"actor": "P1", "text": "出圈发言"})
    # rewriter 走 executor，返回空串 → 降级放行
    assert outcome.allow is True
    assert outcome.flagged is True


@pytest.mark.asyncio
async def test_min_confidence_below_threshold_passes() -> None:
    """confidence 低于 min_confidence 阈值时，视为不确定，放行。"""
    spec = GuardRailSpec(
        enabled=True,
        checks=["in_character"],
        on_violation="block",
        executor="llm",
        min_confidence=0.8,
    )
    guard = build_guardrail(spec)
    registry = _FakeExecutorRegistry(result=False, confidence=0.5)
    ctx = _FakeCtx(registry)
    outcome = await guard.check(ctx, {"actor": "P1", "text": "模糊发言"})
    assert outcome.allow is True


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
