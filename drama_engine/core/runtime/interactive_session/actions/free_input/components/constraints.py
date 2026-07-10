"""剧情约束组件实现。

每种约束负责：
  1. 判断是否允许继续生长
  2. 强制收束时生成过渡 patch
  3. 提供收束提示文本（注入 prompt）
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.components.base import (
    PlotConstraintComponent,
    build_add_scene_patch,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.grow_state import GrowFlowState
from drama_engine.core.runtime.interactive_session.actions.free_input.prompts.constraints import (
    ENDING_HINT_TEMPLATE,
)

logger = logging.getLogger(__name__)


class FreeConstraint(PlotConstraintComponent):
    """自由发展：无任何约束，无限生长。"""

    async def check(self, grow_state: GrowFlowState, ctx: Any) -> bool:
        return True

    async def build_ending_patch(self, grow_state: GrowFlowState, ctx: Any) -> dict[str, Any]:
        # 永远不会被调用（check 始终返回 True）
        raise RuntimeError("FreeConstraint 不应触发收束")

    def hint_text(self, grow_state: GrowFlowState) -> str | None:
        return None


class MaxRoundsConstraint(PlotConstraintComponent):
    """最大轮数约束：达到上限后强制结束。"""

    async def check(self, grow_state: GrowFlowState, ctx: Any) -> bool:
        current_scene = getattr(ctx, "current_scene_id", "")
        return not grow_state.should_force_ending(self._config, current_scene)

    async def build_ending_patch(self, grow_state: GrowFlowState, ctx: Any) -> dict[str, Any]:
        """生成一个"剧情结束"的终止场景。"""
        scene_id = f"grow_end_{grow_state.total_count() + 1}"
        return build_add_scene_patch(
            scene_id,
            ctx,
            scope={"id": "story", "visibility": "public"},
            controller_action={"enabled": False, "kind": "none"},
            publication={
                "messages": [{
                    "audience": {"scope": "story"},
                    "content": {"text": "剧情在此告一段落。"},
                }]
            },
            state=getattr(ctx, "current_state_id", None),
        )

    def hint_text(self, grow_state: GrowFlowState) -> str | None:
        if grow_state.should_hint_ending(self._config, ""):
            return "剧情即将结束，请为当前故事线做一个收束。"
        return None


class EndingBoundConstraint(PlotConstraintComponent):
    """结局约束：朝预设结局收束。"""

    async def check(self, grow_state: GrowFlowState, ctx: Any) -> bool:
        current_scene = getattr(ctx, "current_scene_id", "")
        return not grow_state.should_force_ending(self._config, current_scene)

    async def build_ending_patch(self, grow_state: GrowFlowState, ctx: Any) -> dict[str, Any]:
        """生成过渡场景，choices 指向预设结局。"""
        ending = self._select_ending(ctx)
        target = ending.get("to", "")
        scene_id = f"grow_converge_{grow_state.total_count() + 1}"

        return build_add_scene_patch(
            scene_id,
            ctx,
            scope={"id": "story", "visibility": "public"},
            controller_action={
                "enabled": True,
                "controller": {"type": "human"},
                "kind": "choice",
                "choices": [{"id": "to_ending", "text": "继续", "to": target}],
                "free_input": {"enabled": False},
            },
            publication={
                "messages": [{
                    "audience": {"scope": "story"},
                    "content": {"text": "命运的齿轮已经转动，故事即将迎来结局……"},
                }]
            },
            state=getattr(ctx, "current_state_id", None),
        )

    def hint_text(self, grow_state: GrowFlowState) -> str | None:
        current_scene = ""  # hint 阶段不需要精确 scene_id
        if not grow_state.should_hint_ending(self._config, current_scene):
            return None

        # 构造结局描述
        ending_config = self._config.get("ending") or {}
        candidates = list(ending_config.get("candidates") or [])
        if not candidates:
            return "剧情即将收束。"

        descriptions = []
        for c in candidates:
            ending_id = c.get("id", "unknown")
            ending_to = c.get("to", "")
            descriptions.append(f"  - {ending_id} → {ending_to}")

        return ENDING_HINT_TEMPLATE.format(
            ending_descriptions="\n".join(descriptions),
        )

    def _select_ending(self, ctx: Any) -> dict[str, Any]:
        """选择一个结局（条件匹配或 fallback 第一个）。

        复用 ConditionEvaluator 评估 when 条件。
        """
        ending_config = self._config.get("ending") or {}
        candidates = list(ending_config.get("candidates") or [])
        if not candidates:
            return {"id": "default_ending", "to": ""}

        # 尝试条件匹配
        condition_evaluator = getattr(ctx, "condition_evaluator", None)
        state = getattr(ctx, "state", None)

        if condition_evaluator and state:
            for candidate in candidates:
                when = candidate.get("when")
                if when is None:
                    continue
                try:
                    passed = condition_evaluator.evaluate(when, state)
                    if passed:
                        logger.info("[EndingBoundConstraint] 条件匹配结局: %s", candidate.get("id"))
                        return candidate
                except Exception:
                    continue

        # fallback: 无条件的 candidate，或第一个
        for candidate in candidates:
            if candidate.get("when") is None:
                return candidate
        return candidates[0]


__all__ = ["FreeConstraint", "MaxRoundsConstraint", "EndingBoundConstraint"]
