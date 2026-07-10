"""grow_flow 组件基类。

定义 4 个维度组件 + Generator 的抽象接口。
职责分离：每个组件负责管道的一个明确阶段，互不交叉。

执行顺序：
  Constraint.check() → 是否收束？
    ├─ 是 → Constraint.build_ending_patch() → 结束
    └─ 否 → NarrationStyle.build_prompt()
           → Generator.generate(prompt)
           → NarrationStyle.parse_response(raw)
           → InteractionMode.build_controller_action(parsed)
           → Presentation.build_scene_patch(narration + interaction)
"""

from __future__ import annotations

from typing import Any


def build_add_scene_patch(
    scene_id: str,
    ctx: Any,
    *,
    scope: dict[str, Any],
    controller_action: dict[str, Any],
    publication: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 add_scene patch。

    grow_flow 生成/收束场景的统一出口——所有 Presentation 和 Constraint
    都通过它组装 patch，保证 scene 骨架（type/participants/schedule/
    participant_action）只在此处定义一份。

    参数:
        scene_id: 新场景 id
        ctx: InteractiveExecutionContext（读取 current_scene_id 作为 after）
        scope: 场景 scope
        controller_action: 交互结构
        publication: 发布消息结构
        context: 可选的 scene context（如 cinematic 的 dialogue_history）

    返回:
        add_scene patch dict
    """
    scene: dict[str, Any] = {
        "id": scene_id,
        "type": "scene",
        "scope": scope,
        "participants": {"static": []},
        "schedule": {"mode": "none"},
        "participant_action": {"kind": "none", "response": {"mode": "none"}},
        "controller_action": controller_action,
        "publication": publication,
    }
    if context is not None:
        scene["context"] = context
    return {
        "type": "add_scene",
        "after": getattr(ctx, "current_scene_id", ""),
        "scene": scene,
    }


class GrowFlowComponent:
    """grow_flow 组件基类。所有维度组件继承此类。"""

    def __init__(self, config: dict[str, Any]) -> None:
        """初始化组件。

        参数:
            config: 来自 DSL 对应维度的声明配置
        """
        self._config = config


# ════════════════════════════════════════
# 维度 1：续写风格（NarrationStyle）
# ════════════════════════════════════════

class NarrationStyleComponent(GrowFlowComponent):
    """续写风格组件基类。

    职责：
      - 构造 prompt（决定 LLM 写什么格式的内容）
      - 解析 LLM 响应（把原始输出转为标准化内容结构）

    子类只需声明 STYLE_KEY（对应 NARRATION_PROMPTS 中的风格名）和实现
    parse_response()；build_prompt() 由基类统一按 STYLE_KEY 组装。
    """

    # 对应 prompts/narration_styles.py 中 NARRATION_PROMPTS 的键，子类必须覆盖
    STYLE_KEY: str = ""

    def build_prompt(
        self,
        context: dict[str, Any],
        hint: str | None = None,
    ) -> tuple[str, str]:
        """构造 system_prompt 和 user_prompt。

        按 STYLE_KEY 取出对应风格的 prompt 片段，组装成 (system, user)。

        参数:
            context: 生成上下文（text/messages/state 等）
            hint: 收束提示（由 Constraint 提供，None = 不提示）

        返回:
            (system_prompt, user_prompt) 元组
        """
        # 延迟导入避免基类与 prompts 模块循环依赖
        from drama_engine.core.runtime.interactive_session.actions.free_input.prompts.assembly import (
            assemble_system_prompt,
            assemble_user_prompt,
            extract_recent_messages,
            extract_story_summary,
        )
        from drama_engine.core.runtime.interactive_session.actions.free_input.prompts.narration_styles import (
            NARRATION_PROMPTS,
        )

        assert self.STYLE_KEY, f"{self.__class__.__name__} 未声明 STYLE_KEY"
        prompts = NARRATION_PROMPTS[self.STYLE_KEY]
        messages = context.get("messages", [])
        system = assemble_system_prompt(
            narration_format=prompts["output_format"],
            narration_schema=prompts["schema"],
            writing_style=prompts["writing_style"],
            choices_instruction=context.get("choices_instruction"),
            directive=self._config.get("directive"),
            ending_ids=context.get("ending_ids"),
            story_setting=context.get("story_setting"),
            roles=context.get("roles"),
        )
        user = assemble_user_prompt(
            player_text=context.get("text", ""),
            story_summary=extract_story_summary(messages),
            recent_messages=extract_recent_messages(messages),
            depth=context.get("depth", 0),
            max_depth=context.get("max_depth", 0),
            total_count=context.get("total_count", 0),
            max_count=context.get("max_count", 0),
            hint=hint,
        )
        return system, user

    def parse_response(self, raw: dict[str, Any]) -> dict[str, Any]:
        """解析 LLM 原始响应为标准化内容结构。

        参数:
            raw: LLM 返回的原始 dict

        返回:
            标准化内容字典，格式根据风格不同：
              - plain_narration: {"narration": "text"}
              - dialogue_sequence: {"dialogue_history": [{speaker, text, ...}]}
              - mixed: {"narration": "...", "dialogue_history": [...]}
        """
        raise NotImplementedError


# ════════════════════════════════════════
# 维度 2：互动方式（InteractionMode）
# ════════════════════════════════════════

class InteractionModeComponent(GrowFlowComponent):
    """互动方式组件基类。

    职责：
      - 提供 choices 格式说明（注入 prompt，告诉 LLM 生成什么样的选项）
      - 根据 LLM 解析结果构建 controller_action
    """

    def choices_schema_description(self) -> str | None:
        """返回 choices 格式说明（注入 prompt）。

        返回:
            格式说明字符串，None = 不需要 LLM 生成选项
        """
        raise NotImplementedError

    def build_controller_action(
        self,
        parsed_content: dict[str, Any],
        generator_spec: dict[str, Any],
    ) -> dict[str, Any]:
        """构建完整的 controller_action 字典。

        参数:
            parsed_content: NarrationStyle 解析后的内容（含 choices 等）
            generator_spec: DSL generator 配置（用于递归 free_input 继承）

        返回:
            controller_action dict（含 kind/choices/free_input 等）
        """
        raise NotImplementedError

    def _recursive_free_input(self, generator_spec: dict[str, Any]) -> dict[str, Any]:
        """构造递归 free_input 配置：生成的场景自身也带 grow_flow。

        参数:
            generator_spec: 当前 generator 配置（原样继承到子场景）

        返回:
            free_input 配置 dict
        """
        return {
            "enabled": True,
            "mode": "grow_flow",
            "generator": dict(generator_spec),
        }

    def _human_action(
        self,
        kind: str,
        choices: list[dict[str, Any]],
        free_input: dict[str, Any],
    ) -> dict[str, Any]:
        """构造 human controller_action 骨架。

        参数:
            kind: 交互类型（choice / free_text）
            choices: 选项列表
            free_input: free_input 配置

        返回:
            controller_action dict
        """
        return {
            "enabled": True,
            "controller": {"type": "human"},
            "kind": kind,
            "choices": choices,
            "free_input": free_input,
        }

    @staticmethod
    def _format_choices(parsed_content: dict[str, Any]) -> list[dict[str, Any]]:
        """从解析内容规整 choices 为 {id, text} 列表。"""
        choices = list(parsed_content.get("choices") or [])
        return [
            {"id": str(c.get("id") or f"choice_{i}"), "text": str(c.get("text") or "")}
            for i, c in enumerate(choices)
        ]


# ════════════════════════════════════════
# 维度 3：剧情约束（PlotConstraint）
# ════════════════════════════════════════

class PlotConstraintComponent(GrowFlowComponent):
    """剧情约束组件基类。

    职责：
      - 判断是否允许继续生长
      - 强制收束时生成过渡 patch
      - 提供收束提示文本（注入 prompt）
    """

    async def check(self, grow_state: Any, ctx: Any) -> bool:
        """检查是否允许继续生长。

        参数:
            grow_state: GrowFlowState 实例
            ctx: InteractiveExecutionContext

        返回:
            True = 允许生长，False = 强制收束
        """
        raise NotImplementedError

    async def build_ending_patch(self, grow_state: Any, ctx: Any) -> dict[str, Any]:
        """强制收束时生成过渡场景 patch。

        参数:
            grow_state: GrowFlowState 实例
            ctx: InteractiveExecutionContext

        返回:
            add_scene patch dict
        """
        raise NotImplementedError

    def hint_text(self, grow_state: Any) -> str | None:
        """返回收束提示文本（注入 prompt），None = 不提示。

        参数:
            grow_state: GrowFlowState 实例

        返回:
            提示文本或 None
        """
        return None


# ════════════════════════════════════════
# 维度 4：交互展示（Presentation）
# ════════════════════════════════════════

class PresentationComponent(GrowFlowComponent):
    """交互展示组件基类。

    职责：
      - 把 narration 内容 + controller_action 组装成最终 scene patch
      - 决定 publication 结构和 scene context
    """

    def build_scene_patch(
        self,
        narration: dict[str, Any],
        controller_action: dict[str, Any],
        ctx: Any,
        scene_id: str,
    ) -> dict[str, Any]:
        """组装最终 add_scene patch。

        参数:
            narration: NarrationStyle 解析后的内容
            controller_action: InteractionMode 构建的交互结构
            ctx: InteractiveExecutionContext
            scene_id: 新场景 id

        返回:
            完整的 add_scene patch dict
        """
        raise NotImplementedError


# ════════════════════════════════════════
# 生成器（Generator）
# ════════════════════════════════════════

class GrowFlowGenerator:
    """生成器基类。负责调用 LLM/模板/插件生成原始内容。"""

    def __init__(self, config: dict[str, Any]) -> None:
        """初始化生成器。

        参数:
            config: DSL generator 配置
        """
        self._config = config

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        ctx: Any,
    ) -> dict[str, Any]:
        """调用生成，返回原始响应。

        参数:
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            ctx: InteractiveExecutionContext

        返回:
            LLM 原始响应 dict
        """
        raise NotImplementedError


__all__ = [
    "build_add_scene_patch",
    "GrowFlowComponent",
    "NarrationStyleComponent",
    "InteractionModeComponent",
    "PlotConstraintComponent",
    "PresentationComponent",
    "GrowFlowGenerator",
]
