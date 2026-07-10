"""LLM 分支选项兜底设计器。

当 Generator 未返回 choices 时，通过 LLM 强制生成分支选项，
确保生成的场景不会因缺少 choices 而终止 flow。

逻辑：
  1. 先检查 narration 中是否已有 choices（有则直接返回，等同 passthrough）
  2. 没有 choices 时调用 LLM 根据当前剧情强制生成 N 个分支
  3. LLM 失败时 fallback 到固定模板选项
"""

from __future__ import annotations

import json
import logging
from typing import Any

from drama_engine.core.executor import ExecutorRequest
from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.base import ChoiceDesigner

logger = logging.getLogger(__name__)

# 分支生成 system prompt
CHOICE_FALLBACK_SYSTEM_PROMPT = """\
你是一个互动剧情分支设计师。根据当前剧情内容，为玩家设计分支选项。

要求：
- 每个选项代表一个不同的故事发展方向
- 选项之间要有明显区分度（态度、行动、方向不同）
- 选项文字简短有力（10-20字）
- 选项 id 用英文蛇形命名

输出格式（严格 JSON）：
```json
{
  "choices": [
    {"id": "choice_id_1", "text": "选项文字"},
    {"id": "choice_id_2", "text": "选项文字"},
    {"id": "choice_id_3", "text": "选项文字"}
  ]
}
```
"""

CHOICE_FALLBACK_USER_TEMPLATE = """\
当前剧情内容：
{narration_summary}

请为玩家设计 {count} 个分支选项。
"""


class LLMFallbackChoiceDesigner(ChoiceDesigner):
    """LLM 兜底选项设计器：narration 无 choices 时强制通过 LLM 生成。

    配置项（DSL generation.choice_designer.config）：
      - count (int): 生成选项数量，默认 3
      - model_name (str): 可选，指定模型
    """

    async def design_choices(
        self,
        narration: dict[str, Any],
        context: dict[str, Any],
        ctx: Any,
    ) -> list[dict[str, Any]]:
        """设计玩家选项：有则透传，无则 LLM 生成。

        参数:
            narration: 已生成的剧情内容（含 narration/dialogue_history/choices）
            context: 完整运行时上下文
            ctx: InteractiveExecutionContext

        返回:
            选项列表 [{"id": str, "text": str}, ...]
        """
        # 已有 choices 直接透传
        existing = narration.get("choices")
        if isinstance(existing, list) and len(existing) > 0:
            logger.debug("[LLMFallbackChoiceDesigner] 使用已有 choices, count=%d", len(existing))
            return existing

        # 无 choices，调用 LLM 生成
        logger.info("[LLMFallbackChoiceDesigner] narration 无 choices，触发 LLM 兜底生成")
        choices = await self._generate_via_llm(narration, ctx)
        if choices:
            return choices

        # LLM 也失败，返回固定模板
        logger.warning("[LLMFallbackChoiceDesigner] LLM 生成失败，fallback 到模板选项")
        return self._template_fallback()

    async def _generate_via_llm(
        self,
        narration: dict[str, Any],
        ctx: Any,
    ) -> list[dict[str, Any]]:
        """通过 LLM executor 生成分支选项。"""
        executor_registry = getattr(ctx, "executor_registry", None)
        if executor_registry is None or not executor_registry.has("llm"):
            logger.warning("[LLMFallbackChoiceDesigner] 无可用 llm executor")
            return []

        count = int(self._config.get("count", 3))
        narration_summary = self._build_narration_summary(narration)

        prompt = (
            CHOICE_FALLBACK_SYSTEM_PROMPT
            + "\n---\n\n"
            + CHOICE_FALLBACK_USER_TEMPLATE.format(
                narration_summary=narration_summary,
                count=count,
            )
        )

        config: dict[str, Any] = {}
        if self._config.get("model_name"):
            config["model_name"] = self._config["model_name"]

        request = ExecutorRequest(
            purpose="choice_designer_fallback",
            payload={"prompt": prompt},
            config=config,
        )
        response = await executor_registry.execute("llm", request)

        if not response.success:
            logger.warning("[LLMFallbackChoiceDesigner] LLM 调用失败: %s", response.error)
            return []

        return self._parse_choices(response.data)

    def _build_narration_summary(self, narration: dict[str, Any]) -> str:
        """从 narration 内容构建摘要文本（供 LLM prompt 使用）。"""
        parts: list[str] = []

        text = narration.get("narration")
        if text:
            parts.append(str(text))

        dialogue = narration.get("dialogue_history")
        if isinstance(dialogue, list):
            for entry in dialogue[-5:]:
                if isinstance(entry, dict):
                    speaker = entry.get("speaker", "???")
                    line = entry.get("text", "")
                    parts.append(f"{speaker}: {line}")

        return "\n".join(parts) or "剧情继续推进中。"

    def _parse_choices(self, data: Any) -> list[dict[str, Any]]:
        """解析 LLM 返回的 choices 结构。"""
        if not isinstance(data, dict):
            return []

        choices = data.get("choices")
        if not isinstance(choices, list):
            return []

        result: list[dict[str, Any]] = []
        for i, item in enumerate(choices):
            if not isinstance(item, dict):
                continue
            choice_id = str(item.get("id") or f"fallback_{i}")
            choice_text = str(item.get("text") or "")
            if choice_text:
                result.append({"id": choice_id, "text": choice_text})

        return result

    def _template_fallback(self) -> list[dict[str, Any]]:
        """LLM 不可用时的固定模板选项。"""
        return [
            {"id": "continue_story", "text": "继续探索"},
            {"id": "ask_around", "text": "四处打听"},
            {"id": "take_action", "text": "采取行动"},
        ]


__all__ = ["LLMFallbackChoiceDesigner"]
