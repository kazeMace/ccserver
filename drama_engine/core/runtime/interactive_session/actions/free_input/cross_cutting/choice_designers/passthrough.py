"""透传选项设计器（默认实现）。

直接使用 Generator 一次性产出的 choices，不做二次处理。
用于向后兼容——未配置 choice_designer 时使用此实现。
"""

from __future__ import annotations

from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.base import ChoiceDesigner


class PassthroughChoiceDesigner(ChoiceDesigner):
    """透传设计器：直接返回 narration 中已有的 choices。"""

    async def design_choices(
        self,
        narration: dict[str, Any],
        context: dict[str, Any],
        ctx: Any,
    ) -> list[dict[str, Any]]:
        """直接提取并返回 Generator 产出的 choices。"""
        choices = narration.get("choices")
        if isinstance(choices, list):
            return choices
        return []


__all__ = ["PassthroughChoiceDesigner"]
