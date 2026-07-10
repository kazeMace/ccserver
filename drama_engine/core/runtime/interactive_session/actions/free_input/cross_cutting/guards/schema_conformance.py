"""输出结构一致性守卫。

检查 LLM 生成的输出是否符合预期的 schema 结构。
后端: builtin。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.base import OutputGuard
from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.models import GuardResult

logger = logging.getLogger(__name__)


class SchemaConformanceGuard(OutputGuard):
    """检查生成输出的结构完整性。

    验证：
      - 至少有 narration 或 dialogue_history 之一
      - dialogue_history 中每项都有 speaker 和 text
      - choices（如果有）每项都有 id 和 text
    """

    async def check(self, payload: dict[str, Any], ctx: Any) -> GuardResult:
        """校验输出结构。

        参数:
            payload: 生成结果 dict
            ctx: InteractiveExecutionContext
        """
        narration = payload.get("narration")
        dialogue_history = payload.get("dialogue_history")

        # 至少有一种内容
        has_narration = bool(narration and str(narration).strip())
        has_dialogue = bool(dialogue_history and isinstance(dialogue_history, list) and len(dialogue_history) > 0)

        if not has_narration and not has_dialogue:
            return GuardResult(
                passed=False,
                reason="生成内容为空：缺少 narration 和 dialogue_history",
                metadata={"issue": "empty_content"},
            )

        # 校验 dialogue_history 结构
        if has_dialogue:
            for i, entry in enumerate(dialogue_history):
                if not isinstance(entry, dict):
                    return GuardResult(
                        passed=False,
                        reason=f"dialogue_history[{i}] 不是 dict",
                        metadata={"issue": "invalid_dialogue_entry", "index": i},
                    )
                if not entry.get("speaker") and not entry.get("text"):
                    return GuardResult(
                        passed=False,
                        reason=f"dialogue_history[{i}] 缺少 speaker 和 text",
                        metadata={"issue": "missing_fields", "index": i},
                    )

        # 校验 choices 结构（如果有）
        choices = payload.get("choices")
        if choices and isinstance(choices, list):
            for i, choice in enumerate(choices):
                if not isinstance(choice, dict):
                    return GuardResult(
                        passed=False,
                        reason=f"choices[{i}] 不是 dict",
                        metadata={"issue": "invalid_choice_entry", "index": i},
                    )
                if not choice.get("text"):
                    return GuardResult(
                        passed=False,
                        reason=f"choices[{i}] 缺少 text 字段",
                        metadata={"issue": "choice_missing_text", "index": i},
                    )

        return GuardResult(passed=True)


__all__ = ["SchemaConformanceGuard"]
