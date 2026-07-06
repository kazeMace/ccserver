"""OOC 违规处理策略 / GuardRail violation strategies（策略模式）。

每种 on_violation 策略是一个独立的类，实现统一的 ViolationStrategy 接口。
新增策略只需新增一个子类并在 build_strategy 注册，符合开闭原则（OCP）：
对扩展开放（加新策略），对修改关闭（不改已有策略与调用方）。

策略拿到「判定为违规的发言」后决定如何处理：拦截 / 改写 / 打标放行 / 记录放行。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from drama_engine.core.moderation.models import GuardDecision, GuardOutcome

logger = logging.getLogger(__name__)


class ViolationStrategy(ABC):
    """违规处理策略抽象基类。

    子类实现 apply()，根据判定结果与原发言产出 GuardOutcome。
    rewriter 是可选的「改写器」回调（async 可调用），仅 RewriteStrategy 需要。
    """

    name: str = ""

    @abstractmethod
    async def apply(
        self,
        decision: GuardDecision,
        response: dict[str, Any],
        rewriter: Any = None,
    ) -> GuardOutcome:
        """处理一条违规发言。

        参数：
          decision — 判定结果（此处 decision.violated 恒为 True）。
          response — 原始发言 dict（含 actor / text / data）。
          rewriter — 可选的 async 改写器，签名 rewriter(response, reason) -> str。

        返回：GuardOutcome。
        """
        raise NotImplementedError


class BlockStrategy(ViolationStrategy):
    """拦截：违规发言不投递。"""

    name = "block"

    async def apply(self, decision, response, rewriter=None) -> GuardOutcome:
        """拦下发言，allow=False。"""
        logger.info("[GuardRail] block 违规发言 actor=%s reason=%s", response.get("actor"), decision.reason)
        return GuardOutcome(
            allow=False,
            response=response,
            flagged=True,
            note=f"发言被拦截（{decision.reason or '越界/离题'}）",
        )


class RewriteStrategy(ViolationStrategy):
    """改写：调用改写器把发言拉回场景内，再放行。

    改写器不可用或改写失败时，降级为放行原发言并打标（避免中断游戏）。
    """

    name = "rewrite"

    async def apply(self, decision, response, rewriter=None) -> GuardOutcome:
        """改写发言文本后放行。"""
        if rewriter is None:
            logger.warning("[GuardRail] rewrite 策略无改写器，降级为打标放行")
            return GuardOutcome(
                allow=True,
                response=response,
                flagged=True,
                note="rewrite 无改写器，原样放行并打标",
            )
        try:
            new_text = await rewriter(response, decision.reason)
        except Exception as exc:  # noqa: BLE001 - 改写失败不应中断游戏。
            logger.warning("[GuardRail] 改写失败，降级为打标放行: %s", exc)
            return GuardOutcome(allow=True, response=response, flagged=True, note=f"改写失败: {exc}")
        rewritten = dict(response)
        if new_text:
            rewritten["text"] = str(new_text)
        logger.info("[GuardRail] rewrite 改写发言 actor=%s", response.get("actor"))
        return GuardOutcome(allow=True, response=rewritten, flagged=True, note="发言已改写回场景内")


class SoftWarnStrategy(ViolationStrategy):
    """软警告：照发，但给 host 打标观测。"""

    name = "soft_warn"

    async def apply(self, decision, response, rewriter=None) -> GuardOutcome:
        """放行发言，仅打标。"""
        logger.info("[GuardRail] soft_warn 放行并打标 actor=%s reason=%s", response.get("actor"), decision.reason)
        return GuardOutcome(
            allow=True,
            response=response,
            flagged=True,
            note=f"发言可能越界/离题（{decision.reason or '未说明'}），已放行并标记",
        )


class PassWithFlagStrategy(ViolationStrategy):
    """放行并记录：与 soft_warn 类似但语义上更轻（仅记 flag 事件，不强调警告）。"""

    name = "pass_with_flag"

    async def apply(self, decision, response, rewriter=None) -> GuardOutcome:
        """放行发言，记 flag。"""
        return GuardOutcome(
            allow=True,
            response=response,
            flagged=True,
            note=f"发言已放行并记录标记（{decision.reason or ''}）",
        )


# 策略注册表：策略名 -> 策略类。新增策略在此登记即可（OCP）。
_STRATEGIES: dict[str, type[ViolationStrategy]] = {
    BlockStrategy.name: BlockStrategy,
    RewriteStrategy.name: RewriteStrategy,
    SoftWarnStrategy.name: SoftWarnStrategy,
    PassWithFlagStrategy.name: PassWithFlagStrategy,
}


def build_strategy(name: str) -> ViolationStrategy:
    """按 on_violation 名构建策略实例。

    参数：
      name — 策略名（block | rewrite | soft_warn | pass_with_flag）。
    未知策略名时断言失败（编译期 GuardRailSpec 已校验，此处兜底）。
    """
    assert name in _STRATEGIES, f"未知违规处理策略: {name}，可选: {sorted(_STRATEGIES)}"
    return _STRATEGIES[name]()


__all__ = [
    "ViolationStrategy",
    "BlockStrategy",
    "RewriteStrategy",
    "SoftWarnStrategy",
    "PassWithFlagStrategy",
    "build_strategy",
]
