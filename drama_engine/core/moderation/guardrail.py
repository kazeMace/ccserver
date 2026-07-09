"""OOC 内容守卫 / GuardRail（架构文档 §14 扩展）。

在 agent/玩家发言写入前，判定其是否离题（off-topic）、出圈（out-of-character）
或泄露了不该说的秘密（secret leak），并按配置的策略处理。

GuardRail 是 ABC 基类，定义 judge 接口。具体实现（LLMGuardRail 等）
通过 ExecutorRegistry 调用底层 executor，不依赖 ConditionEvaluator。
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from drama_engine.core.moderation.models import GuardDecision, GuardOutcome, GuardRailSpec
from drama_engine.core.moderation.strategies import ViolationStrategy, build_strategy

logger = logging.getLogger(__name__)


class GuardRail(ABC):
    """内容守卫基类。

    子类实现 judge() 方法提供具体判定逻辑。
    check() 是模板方法：enabled 检查 → judge → strategy.apply。
    """

    def __init__(self, spec: GuardRailSpec, strategy: ViolationStrategy) -> None:
        """初始化守卫。

        参数：
          spec     — 编译后的 GuardRailSpec。
          strategy — 违规处理策略实例。
        """
        assert spec is not None, "GuardRailSpec 不能为空"
        self._spec = spec
        self._strategy = strategy

    @property
    def enabled(self) -> bool:
        """守卫是否启用。"""
        return self._spec.enabled

    @abstractmethod
    async def judge(self, ctx: Any, text: str, actor: str) -> GuardDecision:
        """判定文本是否违规。子类实现具体判定逻辑。

        参数：
          ctx   — InteractiveExecutionContext
          text  — 待判定文本
          actor — 发言者标识

        返回：
          GuardDecision（violated=True 表示违规）
        """
        ...

    async def check(self, ctx: Any, response: dict[str, Any]) -> GuardOutcome:
        """检查一条发言并返回处理结果（模板方法）。

        参数：
          ctx      — InteractiveExecutionContext
          response — 待检查发言 dict（含 actor / text / data）

        返回：GuardOutcome。未启用或无文本时直接放行。
        """
        if not self._spec.enabled:
            return GuardOutcome(allow=True, response=response)
        text = str(response.get("text") or "").strip()
        if not text:
            return GuardOutcome(allow=True, response=response)

        actor = str(response.get("actor") or "")
        decision = await self.judge(ctx, text, actor)
        if not decision.violated:
            return GuardOutcome(allow=True, response=response)
        # 违规：交给策略处理（可拦截 / 改写 / 打标放行）
        return await self._strategy.apply(
            decision, response, rewriter=self._make_rewriter(ctx)
        )

    def _make_rewriter(self, ctx: Any) -> Any:
        """构造改写器回调（仅 rewrite 策略会用到）。

        默认返回 None（子类可覆写提供改写能力）。
        """
        return None


class LLMGuardRail(GuardRail):
    """通过 LLM 做语义判定的 GuardRail。

    使用 ExecutorRegistry 调用 llm executor，不依赖 ConditionEvaluator。
    """

    async def judge(self, ctx: Any, text: str, actor: str) -> GuardDecision:
        """通过 LLM 判定发言是否违规。"""
        executor_registry = getattr(ctx, "executor_registry", None)
        if executor_registry is None:
            logger.debug("[GuardRail] 无 executor_registry，跳过判定")
            return GuardDecision(violated=False, reason="无 executor_registry")

        prompt = self._build_prompt(text)
        from drama_engine.core.executor import ExecutorRequest

        request = ExecutorRequest(
            purpose="guardrail",
            payload={"prompt": prompt},
            config=dict(self._spec.config),
            context=getattr(ctx, "session_metadata", None),
        )
        try:
            response = await executor_registry.execute("llm", request)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[GuardRail] LLM 判定调用失败，保守放行: %s", exc)
            return GuardDecision(violated=False, reason=f"判定异常: {exc}")

        return self._parse_result(response)

    def _build_prompt(self, text: str) -> str:
        """构造 LLM 判定 prompt。"""
        checks_text = "、".join(self._spec.checks) if self._spec.checks else "是否符合当前角色与场景"
        return (
            "你是剧情内容守卫。请判断下面这句发言是否合规——"
            f"需同时满足以下维度：{checks_text}。"
            "合规返回 result=true，越界/离题/泄密返回 result=false。"
            f"\n发言内容：{text}\n请返回 JSON：{{\"result\": bool, \"confidence\": 0~1}}"
        )

    def _parse_result(self, response: Any) -> GuardDecision:
        """解析 ExecutorResponse 为 GuardDecision。"""
        if response is None:
            return GuardDecision(violated=False, reason="executor 返回空")
        # ExecutorResponse.data 是解析后的 dict
        data = getattr(response, "data", None) or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (json.JSONDecodeError, ValueError):
                data = {}
        if not isinstance(data, dict):
            # 尝试从 raw 解析
            raw = getattr(response, "raw", None)
            if isinstance(raw, str):
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    return GuardDecision(violated=False, reason="无法解析判定结果")
            else:
                return GuardDecision(violated=False, reason="无法解析判定结果")

        result = data.get("result")
        confidence = float(data.get("confidence") or 1.0)
        # confidence 低于阈值视为不确定，放行
        if self._spec.min_confidence > 0 and confidence < self._spec.min_confidence:
            return GuardDecision(violated=False, reason=f"confidence {confidence} 低于阈值")
        # result=True → 合规（不违规）；result=False → 违规
        if result is True or result == "true":
            return GuardDecision(violated=False)
        return GuardDecision(violated=True, reason=data.get("reason") or "发言未通过内容守卫判定")

    def _make_rewriter(self, ctx: Any) -> Any:
        """通过 ExecutorRegistry 调用 LLM 做文本改写。"""
        executor_registry = getattr(ctx, "executor_registry", None)
        if executor_registry is None:
            return None

        async def _rewrite(response: dict[str, Any], reason: str) -> str:
            """把发言改写回场景内；失败时返回空串（保留原文）。"""
            original = str(response.get("text") or "")
            prompt = (
                "下面这句发言越界/离题了，请在保持说话人意图的前提下，"
                f"把它改写成符合当前角色与场景的一句话。原因：{reason or '越界/离题'}。"
                f"\n原发言：{original}\n只返回改写后的一句话，不要解释。"
            )
            from drama_engine.core.executor import ExecutorRequest as ER

            request = ER(
                purpose="guardrail_rewrite",
                payload={"prompt": prompt},
                config=dict(self._spec.config),
                context=getattr(ctx, "session_metadata", None),
            )
            try:
                resp = await executor_registry.execute("llm", request)
                # 从 data 或 raw 中提取改写文本
                data = getattr(resp, "data", None) or {}
                if isinstance(data, dict):
                    return str(data.get("text") or data.get("content") or "").strip()
                return str(getattr(resp, "raw", "") or "").strip()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[GuardRail] 改写调用失败: %s", exc)
                return ""

        return _rewrite


def build_guardrail(spec: GuardRailSpec | None) -> GuardRail | None:
    """按 GuardRailSpec 构建 GuardRail 实例；spec 为空或未启用时返回 None。"""
    if spec is None or not spec.enabled:
        return None
    strategy = build_strategy(spec.on_violation)
    executor_type = spec.executor or "llm"
    if executor_type == "llm":
        return LLMGuardRail(spec, strategy)
    raise ValueError(f"不支持的 guardrail executor: {executor_type}")


__all__ = ["GuardRail", "LLMGuardRail", "build_guardrail"]
