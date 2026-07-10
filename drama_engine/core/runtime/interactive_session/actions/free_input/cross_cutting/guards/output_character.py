"""输出角色存在性守卫。

检查 LLM 生成的内容中出现的角色是否都存在于剧本定义中。
后端: builtin。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.base import OutputGuard
from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.models import GuardResult

logger = logging.getLogger(__name__)


class OutputCharacterExistenceGuard(OutputGuard):
    """检查生成内容中的 speaker/角色名是否合法。

    原理：
      1. 从生成的 dialogue_history 中提取所有 speaker
      2. 对照剧本角色列表
      3. 发现不存在的角色时返回失败
    """

    async def check(self, payload: dict[str, Any], ctx: Any) -> GuardResult:
        """校验生成内容中的角色。

        参数:
            payload:
                - dialogue_history: [{"speaker": "Nora", "text": "..."}, ...]
                - narration: 叙述文本
                - _characters: 剧本角色列表（由 pipeline 注入）
            ctx: InteractiveExecutionContext
        """
        dialogue_history = list(payload.get("dialogue_history") or [])
        characters = list(payload.get("_characters") or [])

        if not dialogue_history or not characters:
            return GuardResult(passed=True)

        # 构建合法角色名集合
        valid_names: set[str] = {"narrator", "旁白", "system"}
        for char in characters:
            name = char.get("name", "")
            if name:
                valid_names.add(name.lower())
            names = char.get("names", "")
            if isinstance(names, str):
                for n in names.split(","):
                    n = n.strip()
                    if n:
                        valid_names.add(n.lower())
            elif isinstance(names, list):
                for n in names:
                    if n:
                        valid_names.add(str(n).lower())

        # 检查每一行对话的 speaker
        invalid_speakers: list[str] = []
        for entry in dialogue_history:
            speaker = str(entry.get("speaker", "")).strip()
            if not speaker:
                continue
            if speaker.lower() not in valid_names:
                invalid_speakers.append(speaker)

        if invalid_speakers:
            unique_invalid = list(set(invalid_speakers))
            logger.info(
                "[OutputCharacterExistenceGuard] 发现不存在的角色: %s", unique_invalid
            )
            return GuardResult(
                passed=False,
                reason=f"生成内容中出现了不存在的角色: {', '.join(unique_invalid)}",
                metadata={"invalid_speakers": unique_invalid},
            )

        return GuardResult(passed=True)


__all__ = ["OutputCharacterExistenceGuard"]
