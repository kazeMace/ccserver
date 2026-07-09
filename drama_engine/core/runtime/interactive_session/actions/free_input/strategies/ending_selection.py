"""结局选择策略（Ending Selection）。

在约束续写（constrained_continue）模式中选择预设结局。

适用场景：
  - 文字冒险：根据游戏状态选择好结局/坏结局/真结局
  - 互动小说：根据剧情进度选择多结局分支
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.strategies.base import (
    FreeInputStrategy,
)

logger = logging.getLogger(__name__)


class ConditionEndingSelectionStrategy(FreeInputStrategy):
    """基于条件判断的结局选择（内置实现）。

    算法：
      1. 遍历 ending.candidates 列表
      2. 对每个结局的 when 条件进行判断
      3. 返回第一个条件为 true 的结局
      4. 如果都不满足，返回第一个结局作为 fallback

    需要配合 ConditionEvaluator 使用。
    """

    async def execute(
        self,
        mode: str,
        spec: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """选择结局。

        参数:
            mode: 固定为 "constrained_continue"
            spec: DSL free_input 配置，包含 ending.candidates 和 ending.selector
            context: 包含 ctx（InteractiveExecutionContext）、state、condition_evaluator

        返回:
            {
                "ending": 选中的结局 id 或 name
            }
        """
        ending_spec = spec.get("ending", {}) if isinstance(spec.get("ending"), dict) else {}
        candidates = list(ending_spec.get("candidates", []))

        if not candidates:
            logger.warning("[ConditionEndingSelection] 没有候选结局，返回空")
            return {"ending": None}

        # 从 context 提取 condition executor
        ctx = context.get("ctx")
        condition_evaluator = context.get("condition_evaluator")
        if not condition_evaluator and ctx:
            condition_evaluator = getattr(ctx, "condition_evaluator", None)

        # 遍历候选结局，检查条件
        for candidate in candidates:
            if not isinstance(candidate, dict):
                # 简单形式：只有 id/name 字符串
                ending_id = str(candidate)
                logger.debug(
                    "[ConditionEndingSelection] 候选结局无条件，直接返回: %s",
                    ending_id,
                )
                return {"ending": ending_id}

            # 复杂形式：包含 when 条件
            when = candidate.get("when")
            if not isinstance(when, dict):
                # 无条件，直接返回
                ending_id = candidate.get("id") or candidate.get("name")
                logger.debug(
                    "[ConditionEndingSelection] 候选结局无条件，直接返回: %s",
                    ending_id,
                )
                return {"ending": ending_id}

            # 判断条件
            if condition_evaluator:
                try:
                    result = condition_evaluator.evaluate(
                        when,
                        context.get("state"),
                        context.get("actor"),
                    )
                    if result:
                        ending_id = candidate.get("id") or candidate.get("name")
                        logger.info(
                            "[ConditionEndingSelection] 条件满足，选择结局: %s",
                            ending_id,
                        )
                        return {"ending": ending_id}
                except Exception as exc:
                    logger.warning(
                        "[ConditionEndingSelection] 条件判断异常: %s，跳过该候选",
                        exc,
                    )
                    continue

        # Fallback: 返回第一个结局
        first = candidates[0]
        ending_id = first.get("id") or first.get("name") if isinstance(first, dict) else str(first)
        logger.info(
            "[ConditionEndingSelection] 无条件满足，fallback 到第一个结局: %s",
            ending_id,
        )
        return {"ending": ending_id}


__all__ = ["ConditionEndingSelectionStrategy"]
