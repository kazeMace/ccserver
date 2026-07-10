"""内容安全输入守卫。

基础实现：关键词过滤。
可扩展为 LLM/HTTP 后端进行更精确的内容安全检测。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.base import InputGuard
from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.models import GuardResult

logger = logging.getLogger(__name__)

# 基础违规关键词（实际生产中应使用更完善的过滤系统）
_DEFAULT_BLOCKED_PATTERNS: list[str] = []


class ContentSafetyInputGuard(InputGuard):
    """内容安全过滤守卫。

    builtin 模式：基于关键词黑名单过滤。
    llm/http 模式：调用外部服务判断（需要 adapter 层支持）。
    """

    async def check(self, payload: dict[str, Any], ctx: Any) -> GuardResult:
        """校验内容安全性。

        参数:
            payload:
                - text: 玩家输入文本
            ctx: InteractiveExecutionContext
        """
        text = str(payload.get("text", ""))
        if not text:
            return GuardResult(passed=True)

        # builtin 模式：关键词过滤
        blocked_patterns = list(self._config.get("blocked_patterns") or _DEFAULT_BLOCKED_PATTERNS)
        text_lower = text.lower()

        for pattern in blocked_patterns:
            if pattern.lower() in text_lower:
                logger.info("[ContentSafetyInputGuard] 输入触发安全过滤: pattern=%s", pattern)
                return GuardResult(
                    passed=False,
                    reason="输入内容包含不适当内容，请重新输入。",
                    metadata={"triggered_pattern": pattern},
                )

        return GuardResult(passed=True)


__all__ = ["ContentSafetyInputGuard"]
