"""选项映射策略（Choose Mapping）。

把玩家的自由文本输入映射到最接近的固定选项。

适用场景：
  - 文字冒险：输入"走左边那条路" → 映射到"向左走"选项
  - 狼人杀投票：输入"我投1号" → 映射到 vote_player_1
  - 桌游行动：输入"移动到红色格子" → 映射到 move_red
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.strategies.base import (
    FreeInputStrategy,
)


class DifflibChoiceMappingStrategy(FreeInputStrategy):
    """基于字符串相似度的选项映射（内置实现）。

    算法：
      1. 用 difflib.SequenceMatcher 计算输入与每个选项的相似度
      2. 如果输入包含选项 id 或 text 的子串，额外加 1.0 分
      3. 返回得分最高的选项

    优点：无需外部服务，响应快
    缺点：无法理解语义，如"不去"可能误匹配"去"
    """

    async def execute(
        self,
        mode: str,
        spec: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """执行选项映射。

        参数:
            mode: 固定为 "choose_mapping"
            spec: DSL free_input 配置
            context: 包含 text（玩家输入）和 choices（可选分支）

        返回:
            {
                "selected_choice": 选中的选项 id,
                "to": 跳转目标（从 choice.to 提取）,
                "confidence": 匹配置信度（0.0-2.0+）
            }
        """
        text = str(context.get("text", "")).lower()
        choices = list(context.get("choices", []))

        if not choices:
            return {
                "selected_choice": None,
                "to": None,
                "confidence": 0.0,
            }

        best_choice = choices[0]
        best_score = -1.0

        for choice in choices:
            choice_id = str(choice.get("id", ""))
            choice_text = str(choice.get("text", ""))
            haystack = (choice_id + " " + choice_text).lower()

            # 基础相似度（0.0-1.0）
            score = SequenceMatcher(None, text, haystack).ratio()

            # 关键词命中加分：如果输入包含选项 id 或 text 的任意子串，+1.0
            if text and (choice_id.lower() in text or choice_text.lower() in text):
                score += 1.0

            if score > best_score:
                best_score = score
                best_choice = choice

        return {
            "selected_choice": best_choice.get("id"),
            "to": best_choice.get("to"),
            "confidence": max(0.0, best_score),
        }


__all__ = ["DifflibChoiceMappingStrategy"]
