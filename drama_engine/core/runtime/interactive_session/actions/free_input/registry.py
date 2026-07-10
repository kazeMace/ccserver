"""自由输入策略注册表。

管理 5 种自由输入策略的注册与查找。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.strategies.base import (
    FreeInputStrategy,
)

logger = logging.getLogger(__name__)


class FreeInputStrategyRegistry:
    """自由输入策略注册表。

    负责：
      1. 初始化时注册内置策略（builtin 实现）
      2. 运行时允许注册/替换策略（plugin / game pack）
      3. 根据 mode 查找策略实例
    """

    def __init__(self):
        """初始化注册表，注册内置策略。"""
        from drama_engine.core.runtime.interactive_session.actions.free_input.grow_flow_pipeline import (
            GrowFlowStrategy,
        )
        from drama_engine.core.runtime.interactive_session.actions.free_input.strategies.choice_mapping import (
            DifflibChoiceMappingStrategy,
        )
        from drama_engine.core.runtime.interactive_session.actions.free_input.strategies.content_generation import (
            FixedTextContentGenerationStrategy,
        )
        from drama_engine.core.runtime.interactive_session.actions.free_input.strategies.ending_selection import (
            ConditionEndingSelectionStrategy,
        )
        from drama_engine.core.runtime.interactive_session.actions.free_input.strategies.flow_patch_generation import (
            TemplateFlowPatchGenerationStrategy,
        )

        # 注册内置策略
        self._strategies: dict[str, FreeInputStrategy] = {
            "choose_mapping": DifflibChoiceMappingStrategy(),
            "branch_then_return": FixedTextContentGenerationStrategy(),
            "constrained_continue": FixedTextContentGenerationStrategy(),
            "free_continue": FixedTextContentGenerationStrategy(),
            "grow_flow": GrowFlowStrategy(),
        }

        # 单独的结局选择策略（constrained_continue 需要）
        self._ending_selector = ConditionEndingSelectionStrategy()

        logger.debug("[FreeInputStrategyRegistry] 已注册 %d 个内置策略", len(self._strategies))

    def register(self, mode: str, strategy: FreeInputStrategy) -> None:
        """注册或替换策略。

        Game Pack 或插件可以调用这个方法注册增强版策略。

        参数:
            mode: 策略模式名称（choose_mapping/branch_then_return/...）
            strategy: 策略实例
        """
        assert mode, "mode 不能为空"
        assert isinstance(strategy, FreeInputStrategy), "strategy 必须是 FreeInputStrategy 实例"

        old_strategy = self._strategies.get(mode)
        self._strategies[mode] = strategy

        logger.info(
            "[FreeInputStrategyRegistry] 注册策略: mode=%s strategy=%s %s",
            mode,
            strategy.__class__.__name__,
            "(替换)" if old_strategy else "(新增)",
        )

    def get(self, mode: str) -> FreeInputStrategy | None:
        """获取策略实例。

        参数:
            mode: 策略模式名称

        返回:
            策略实例，如果未注册则返回 None
        """
        return self._strategies.get(mode)

    def get_ending_selector(self) -> FreeInputStrategy:
        """获取结局选择策略（constrained_continue 专用）。

        返回:
            结局选择策略实例
        """
        return self._ending_selector

    def list_modes(self) -> list[str]:
        """列出所有已注册的模式名称。

        返回:
            模式名称列表
        """
        return list(self._strategies.keys())


__all__ = ["FreeInputStrategyRegistry"]
