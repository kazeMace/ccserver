"""OOC 内容守卫 / GuardRail（架构文档 §14 扩展）。

在 agent/玩家发言写入前，判定其是否离题（off-topic）、出圈（out-of-character）
或泄露了不该说的秘密（secret leak），并按配置的策略处理。

判定复用现有条件体系 ConditionEvaluator（evaluator: llm / provider: inside），
不新造 LLM 调用代码——天然获得 confidence 门限与 fallback 语义。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.moderation.models import GuardDecision, GuardOutcome, GuardRailSpec
from drama_engine.core.moderation.strategies import ViolationStrategy, build_strategy

logger = logging.getLogger(__name__)


class GuardRail:
    """按 GuardRailSpec 检查发言内容并处理违规。"""

    def __init__(self, spec: GuardRailSpec) -> None:
        """初始化守卫。

        参数：
          spec — 编译后的 GuardRailSpec。
        """
        assert spec is not None, "GuardRailSpec 不能为空"
        self._spec = spec
        # 违规处理策略：按 on_violation 选定（策略模式）。
        self._strategy: ViolationStrategy = build_strategy(spec.on_violation)

    @property
    def enabled(self) -> bool:
        """守卫是否启用。"""
        return self._spec.enabled

    async def check(self, ctx: Any, response: dict[str, Any]) -> GuardOutcome:
        """检查一条发言并返回处理结果。

        参数：
          ctx      — InteractiveExecutionContext（提供 condition_evaluator / state / condition_extra）。
          response — 待检查发言 dict（含 actor / text / data）。

        返回：GuardOutcome。未启用或无文本时直接放行（不打标、零 LLM 调用）。
        """
        # 未启用：完全旁路。
        if not self._spec.enabled:
            return GuardOutcome(allow=True, response=response)
        text = str(response.get("text") or "").strip()
        if not text:
            return GuardOutcome(allow=True, response=response)

        decision = await self._judge(ctx, response, text)
        if not decision.violated:
            # 合规：直接放行。
            return GuardOutcome(allow=True, response=response)
        # 违规：交给策略处理（可拦截 / 改写 / 打标放行）。
        return await self._strategy.apply(decision, response, rewriter=self._make_rewriter(ctx))

    async def _judge(self, ctx: Any, response: dict[str, Any], text: str) -> GuardDecision:
        """调用 LLM 判定发言是否违规。

        构造 executor 条件 dict，走 ConditionEvaluator.evaluate_async；
        约定：条件为真表示「合规」，为假表示「违规」。判定失败按 fallback（默认合规）处理。
        """
        evaluator = ctx.condition_evaluator
        if evaluator is None:
            return GuardDecision(violated=False, reason="无 condition_evaluator，跳过判定")
        cond = self._build_condition(text)
        try:
            passed = await evaluator.evaluate_async(
                cond,
                ctx.state,
                actor=str(response.get("actor") or ""),
                extra=ctx.condition_extra(),
            )
        except Exception as exc:  # noqa: BLE001 - 判定失败不应中断游戏。
            logger.warning("[GuardRail] 判定失败，保守放行: %s", exc)
            return GuardDecision(violated=False, reason=f"判定异常: {exc}")
        # passed=True → 合规（不违规）；passed=False → 违规。
        return GuardDecision(violated=not passed, reason="" if passed else "发言未通过内容守卫判定")

    def _build_condition(self, text: str) -> dict[str, Any]:
        """把 GuardRailSpec.executor 与检查维度组装成 ConditionEvaluator 条件 dict。"""
        checks_text = "、".join(self._spec.checks) if self._spec.checks else "是否符合当前角色与场景"
        default_prompt = (
            "你是剧情内容守卫。请判断下面这句发言是否合规——"
            f"需同时满足以下维度：{checks_text}。"
            "合规返回 result=true，越界/离题/泄密返回 result=false。"
            f"\n发言内容：{text}\n请返回 JSON：{{\"result\": bool, \"confidence\": 0~1}}"
        )
        cond = dict(self._spec.executor or {})
        # 归一化 executor 字段：DSL 里可能写 kind: llm，这里统一成 executor: llm。
        if "executor" not in cond:
            cond["executor"] = str(cond.pop("kind", None) or "llm")
        cond.setdefault("provider", "inside")
        cond.setdefault("prompt", default_prompt)
        cond.setdefault("fallback", True)
        cond["_guard_text"] = text
        return cond

    def _make_rewriter(self, ctx: Any) -> Any:
        """构造改写器回调（仅 rewrite 策略会用到）。

        直接复用 inside ccserver Agent（provider: inside）做文本改写：把越界发言
        改写回场景内。条件求值 evaluate_async 只返回 bool，拿不到文本，因此改写走
        独立的 Agent.run 文本通道。无法获取 client 时返回 None，由 RewriteStrategy 降级。
        """
        client = self._resolve_inside_client(ctx)
        if client is None:
            return None

        async def _rewrite(response: dict[str, Any], reason: str) -> str:
            """把发言改写回场景内；失败时返回空串（保留原文）。"""
            original = str(response.get("text") or "")
            prompt = (
                "下面这句发言越界/离题了，请在保持说话人意图的前提下，"
                f"把它改写成符合当前角色与场景的一句话。原因：{reason or '越界/离题'}。"
                f"\n原发言：{original}\n只返回改写后的一句话，不要解释。"
            )
            try:
                text = await self._run_client(client, prompt)
                return str(text or "").strip()
            except Exception as exc:  # noqa: BLE001 - 改写失败保留原文。
                logger.warning("[GuardRail] 改写调用失败: %s", exc)
                return ""

        return _rewrite

    def _resolve_inside_client(self, ctx: Any) -> Any:
        """解析可用的 inside LLM/Agent client（与 executor 同源）。"""
        metadata = getattr(ctx, "session_metadata", None) or {}
        client = (
            metadata.get("inside_agent")
            or metadata.get("llm_client")
            or metadata.get("llm_provider")
        )
        if client is not None:
            return client
        try:
            from drama_engine.core.runtime.interactive_session.services.inside_agent import (
                InsideAgentFactory,
            )

            return InsideAgentFactory().get_or_create(metadata, dict(self._spec.executor or {}))
        except Exception as exc:  # noqa: BLE001 - 无 client 时由调用方降级。
            logger.debug("[GuardRail] 无法获取 inside client: %s", exc)
            return None

    async def _run_client(self, client: Any, prompt: str) -> Any:
        """按 client 支持的接口调用其文本生成能力（run / act / complete / callable）。"""
        if hasattr(client, "run"):
            result = client.run(prompt)
        elif hasattr(client, "act"):
            result = client.act(prompt, None)
        elif hasattr(client, "complete"):
            result = client.complete(prompt)
        elif callable(client):
            result = client(prompt)
        else:
            return ""
        # 兼容 async 与 sync client。
        if hasattr(result, "__await__"):
            result = await result
        if isinstance(result, dict):
            return result.get("text") or result.get("content") or ""
        return result


def build_guardrail(spec: GuardRailSpec | None) -> GuardRail | None:
    """按 GuardRailSpec 构建 GuardRail；spec 为空或未启用时返回 None。"""
    if spec is None or not spec.enabled:
        return None
    return GuardRail(spec)


__all__ = ["GuardRail", "build_guardrail"]
