"""角色存在性输入守卫。

检查玩家输入中提到的角色名是否存在于剧本角色列表中。
后端: builtin（纯字符串匹配，不需要 LLM）。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.base import InputGuard
from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.models import GuardResult

logger = logging.getLogger(__name__)


class CharacterExistenceInputGuard(InputGuard):
    """检查输入中提及的角色是否存在于剧本定义中。

    原理：
      1. 从 ctx 获取剧本中所有角色名
      2. 在玩家输入中搜索角色名模式
      3. 如果发现不存在的角色名，返回拒绝
    """

    async def check(self, payload: dict[str, Any], ctx: Any) -> GuardResult:
        """校验玩家输入中的角色引用。

        参数:
            payload:
                - text: 玩家输入文本
                - characters: 剧本角色列表 [{"name": "Nora", ...}, ...]
            ctx: InteractiveExecutionContext
        """
        text = str(payload.get("text", ""))
        characters = list(payload.get("characters") or [])
        if not text or not characters:
            return GuardResult(passed=True)

        # 提取所有合法角色名（包含别名）
        valid_names: set[str] = set()
        for char in characters:
            name = char.get("name", "")
            if name:
                valid_names.add(name.lower())
            # 支持 names 字段（多个别名）
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

        if not valid_names:
            return GuardResult(passed=True)

        # 检测输入中是否有"像角色名"但不在合法列表中的词
        # 策略：用大写开头的词作为候选角色名（英文）
        # 对于中文场景，跳过此检查（中文名无法通过首字母大写区分）
        mentioned_unknown: list[str] = []
        # 英文：提取首字母大写的单词
        capitalized_words = re.findall(r'\b[A-Z][a-z]+\b', text)
        for word in capitalized_words:
            if word.lower() not in valid_names:
                # 排除常见英文词汇（句首大写等）
                common_words = {"the", "this", "that", "what", "when", "where", "how", "yes", "no",
                                "please", "thank", "sorry", "okay", "hello", "goodbye"}
                if word.lower() not in common_words:
                    mentioned_unknown.append(word)

        if mentioned_unknown:
            valid_list = ", ".join(sorted(valid_names))
            return GuardResult(
                passed=False,
                reason=f"输入中提到了不存在的角色: {', '.join(mentioned_unknown)}。可用角色: {valid_list}",
                suggestions=[f"请使用已有角色名: {valid_list}"],
                metadata={"unknown_characters": mentioned_unknown},
            )

        return GuardResult(passed=True)


__all__ = ["CharacterExistenceInputGuard"]
