"""互动方式组件实现。

每种互动方式负责：
  1. 提供 choices 格式说明（注入 prompt）
  2. 根据 LLM 解析结果构建 controller_action

controller_action / free_input / choices 的骨架由基类 InteractionModeComponent
的 _human_action / _recursive_free_input / _format_choices 辅助方法提供。
"""

from __future__ import annotations

from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.components.base import (
    InteractionModeComponent,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.prompts.interaction_modes import (
    INTERACTION_PROMPTS,
)


class BranchChoiceMode(InteractionModeComponent):
    """分支选择：关键时刻出选项（含自由输入 fallback）。"""

    def choices_schema_description(self) -> str | None:
        template = str(INTERACTION_PROMPTS["branch_choice"]["choices_instruction"])
        choices_count = self._config.get("choices_count") or [2, 4]
        return template.format(min=choices_count[0], max=choices_count[1])

    def build_controller_action(
        self,
        parsed_content: dict[str, Any],
        generator_spec: dict[str, Any],
    ) -> dict[str, Any]:
        action = self._human_action(
            kind="choice",
            choices=self._format_choices(parsed_content),
            free_input=self._recursive_free_input(generator_spec),
        )
        # 生成场景继承 auto_advance，确保 cinematic 逐句播放
        action["controller"] = {"type": "human", "auto_advance": True}
        return action


class FreeInputOnlyMode(InteractionModeComponent):
    """纯自由输入：无固定选项，完全自由文本。"""

    def choices_schema_description(self) -> str | None:
        return INTERACTION_PROMPTS["free_input_only"]["choices_instruction"]

    def build_controller_action(
        self,
        parsed_content: dict[str, Any],
        generator_spec: dict[str, Any],
    ) -> dict[str, Any]:
        return self._human_action(
            kind="free_text",
            choices=[],
            free_input=self._recursive_free_input(generator_spec),
        )


class ConfirmAdvanceMode(InteractionModeComponent):
    """确认推进：只需点"继续"。"""

    def choices_schema_description(self) -> str | None:
        return INTERACTION_PROMPTS["confirm_advance"]["choices_instruction"]

    def build_controller_action(
        self,
        parsed_content: dict[str, Any],
        generator_spec: dict[str, Any],
    ) -> dict[str, Any]:
        # 确认模式不递归 grow_flow（点继续后由 flow 自然推进）
        return self._human_action(
            kind="choice",
            choices=[{"id": "continue", "text": "继续"}],
            free_input={"enabled": False},
        )


__all__ = ["BranchChoiceMode", "FreeInputOnlyMode", "ConfirmAdvanceMode"]
