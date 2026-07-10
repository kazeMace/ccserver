"""grow_flow 执行管道和策略入口。

GrowFlowPipeline：按职责分离顺序调用各组件。
GrowFlowStrategy：FreeInputStrategy 实现，对外统一接口，对内调用 Pipeline。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.components.base import (
    GrowFlowGenerator,
    InteractionModeComponent,
    NarrationStyleComponent,
    PlotConstraintComponent,
    PresentationComponent,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.grow_state import GrowFlowState
from drama_engine.core.runtime.interactive_session.actions.free_input.strategies.base import (
    FreeInputStrategy,
)

logger = logging.getLogger(__name__)


def _short_uuid() -> str:
    """生成 8 字符短 uuid。"""
    return uuid.uuid4().hex[:8]


def _extract_ending_ids(constraint_config: dict[str, Any]) -> list[str]:
    """从 constraint 配置提取 ending_id 列表。"""
    ending = constraint_config.get("ending") or {}
    return [str(c.get("id", "")) for c in (ending.get("candidates") or []) if c.get("id")]


def _extract_story_setting(ctx: Any) -> str:
    """从 ctx.script.meta 提取剧本设定/大纲，作为生成的世界观锚点。

    优先取 description（剧本梗概），其次 title/display_name。
    这是"软约束"的一部分：告诉 LLM 整个故事的基调，避免自由扩展跑偏。

    参数:
        ctx: InteractiveExecutionContext

    返回:
        设定文本，无则返回空串
    """
    script = getattr(ctx, "script", None)
    if script is None:
        return ""
    meta = getattr(script, "meta", None) or {}
    parts = []
    title = meta.get("display_name") or meta.get("title") or meta.get("name")
    if title:
        parts.append(f"《{title}》")
    description = meta.get("description")
    if description:
        parts.append(str(description))
    return " ".join(parts).strip()


def _extract_roles(ctx: Any) -> list[dict[str, Any]]:
    """从 ctx.script.roles 提取角色人设列表，作为生成的角色锚点。

    这是"软约束"的一部分：告诉 LLM 每个角色是谁、性格如何，
    确保生成的对话符合人设、不会凭空捏造角色。

    参数:
        ctx: InteractiveExecutionContext

    返回:
        角色 dict 列表（含 display_name/description），无则返回空列表
    """
    script = getattr(ctx, "script", None)
    if script is None:
        return []
    roles = getattr(script, "roles", None)
    return roles if isinstance(roles, list) else []


class GrowFlowPipeline:
    """grow_flow 执行管道：按职责分离顺序调用各组件。

    执行顺序：
      1. Constraint.check() → 是否收束
      2. NarrationStyle.build_prompt() → 构造 prompt
      3. Generator.generate() → 调用 LLM
      4. NarrationStyle.parse_response() → 解析响应
      5. InteractionMode.build_controller_action() → 构建交互
      6. Presentation.build_scene_patch() → 组装 patch
    """

    def __init__(
        self,
        constraint: PlotConstraintComponent,
        narration: NarrationStyleComponent,
        interaction: InteractionModeComponent,
        presentation: PresentationComponent,
        generator: GrowFlowGenerator,
    ) -> None:
        """绑定各组件。"""
        self._constraint = constraint
        self._narration = narration
        self._interaction = interaction
        self._presentation = presentation
        self._generator = generator

    async def execute(
        self,
        ctx: Any,
        spec: dict[str, Any],
        player_text: str,
        grow_state: GrowFlowState,
    ) -> dict[str, Any]:
        """执行完整管道，返回 {"patch": {...}}。

        参数:
            ctx: InteractiveExecutionContext
            spec: DSL generator 配置块
            player_text: 玩家输入文本
            grow_state: 生长状态追踪器

        返回:
            {"patch": add_scene_patch_dict}
        """
        # 1. Constraint：是否允许继续生长
        if not await self._constraint.check(grow_state, ctx):
            logger.info("[GrowFlowPipeline] 触发强制收束")
            patch = await self._constraint.build_ending_patch(grow_state, ctx)
            return {"patch": patch, "parsed": {}}

        # 2. 构造生成上下文
        messages = getattr(ctx, "message_history", [])
        constraint_config = spec.get("constraint") or {}
        ending_ids = _extract_ending_ids(constraint_config)
        current_scene = getattr(ctx, "current_scene_id", "")

        # choices 格式指令（由 InteractionMode 提供）
        choices_instruction = self._interaction.choices_schema_description()

        # 收束提示（由 Constraint 提供）
        hint = self._constraint.hint_text(grow_state)

        # 软约束：剧本设定 + 角色人设（注入 prompt，锚定自由扩展的方向）
        story_setting = _extract_story_setting(ctx)
        roles = _extract_roles(ctx)

        # 组装 prompt 上下文
        prompt_context = {
            "text": player_text,
            "messages": messages,
            "choices_instruction": choices_instruction,
            "ending_ids": ending_ids,
            "depth": grow_state.depth_of(current_scene) + 1,
            "max_depth": int(constraint_config.get("max_depth") or 0),
            "total_count": grow_state.total_count(),
            "max_count": int(constraint_config.get("max_count") or 0),
            "story_setting": story_setting,
            "roles": roles,
        }

        # 3. NarrationStyle：构造 prompt
        system_prompt, user_prompt = self._narration.build_prompt(prompt_context, hint)

        # 4. Generator：调用 LLM
        raw = await self._generator.generate(system_prompt, user_prompt, ctx)

        # 5. NarrationStyle：解析响应
        parsed = self._narration.parse_response(raw)

        # 6. 如果 LLM 主动收束（should_end=true），处理 ending 指向
        if parsed.get("should_end") and parsed.get("ending_id"):
            self._apply_ending_to_choices(parsed, constraint_config)

        # 7. InteractionMode：构建 controller_action
        controller_action = self._interaction.build_controller_action(parsed, spec)

        # 8. Presentation：组装最终 scene patch
        scene_id = f"grow_{_short_uuid()}"
        patch = self._presentation.build_scene_patch(
            narration=parsed,
            controller_action=controller_action,
            ctx=ctx,
            scene_id=scene_id,
        )

        logger.info("[GrowFlowPipeline] 生成场景: scene_id=%s", scene_id)
        return {"patch": patch, "parsed": parsed}

    def _apply_ending_to_choices(
        self,
        parsed: dict[str, Any],
        constraint_config: dict[str, Any],
    ) -> None:
        """LLM 返回 should_end=true 时，把所有 choices 的 to 指向 ending 目标。"""
        ending_id = parsed.get("ending_id")
        ending = constraint_config.get("ending") or {}

        target = ""
        for c in ending.get("candidates") or []:
            if c.get("id") == ending_id:
                target = str(c.get("to") or "")
                break

        if target and parsed.get("choices"):
            for choice in parsed["choices"]:
                if isinstance(choice, dict):
                    choice["to"] = target


class GrowFlowStrategy(FreeInputStrategy):
    """grow_flow 策略入口。

    对外：标准 FreeInputStrategy 接口（和 choose_mapping 同级）。
    对内：通过 GrowFlowComponentRegistry 解析组件组合，构造 Pipeline 执行。
    """

    async def execute(
        self,
        mode: str,
        spec: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """执行 grow_flow 策略。

        参数:
            mode: 固定为 "grow_flow"
            spec: DSL free_input 完整配置
            context: 运行时上下文（包含 text、ctx、grow_state 等）

        返回:
            {"patch": add_scene_patch_dict}
        """
        # 解析 pipeline 配置块。新语法把各维度配置嵌套在 free_input.generation 下，
        # 旧语法用 free_input.generator，最老的写法直接平铺在 free_input 顶层。
        # 三者按优先级回退，保证 constraint / narration_style / presentation 等
        # 嵌套配置能被正确读取（否则会静默退化成默认组件）。
        generator_spec = spec.get("generation") or spec.get("generator") or spec
        ctx = context.get("ctx")
        player_text = str(context.get("text") or "")
        grow_state = context.get("grow_state")

        # 从共享注册表解析组件组合 → Pipeline（注册表进程内复用，不每回合重建）
        registry = _get_grow_flow_registry()
        pipeline = registry.resolve_pipeline(generator_spec)

        # 执行管道
        return await pipeline.execute(
            ctx=ctx,
            spec=generator_spec,
            player_text=player_text,
            grow_state=grow_state,
        )


# 进程内共享的默认组件注册表。注册表内容是静态的（仅 GamePack 在启动时扩展），
# 无需每个玩家回合重建，懒加载一次后复用。
_DEFAULT_REGISTRY: Any = None


def _get_grow_flow_registry() -> Any:
    """返回进程内共享的默认 grow_flow 组件注册表（首次调用时构建）。"""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        from drama_engine.core.runtime.interactive_session.actions.free_input.grow_flow_registry import (
            build_default_grow_flow_registry,
        )
        _DEFAULT_REGISTRY = build_default_grow_flow_registry()
    return _DEFAULT_REGISTRY


__all__ = ["GrowFlowPipeline", "GrowFlowStrategy"]
