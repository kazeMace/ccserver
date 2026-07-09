"""空规划器（默认实现）。

不做任何规划，直接返回空的 PlanResult。
用于向后兼容——未配置 planner 时使用此实现。
"""

from __future__ import annotations

from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.base import Planner
from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.models import PlanResult


class NullPlanner(Planner):
    """空规划器：不做规划，直接跳过。"""

    async def plan(
        self,
        player_action: str,
        context: dict[str, Any],
        ctx: Any,
    ) -> PlanResult:
        """返回空规划结果。"""
        return PlanResult()


__all__ = ["NullPlanner"]
