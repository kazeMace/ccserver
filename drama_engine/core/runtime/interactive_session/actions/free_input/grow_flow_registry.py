"""grow_flow 组件注册表。

管理各维度组件的注册与查找，根据 DSL spec 解析组件组合并构造 Pipeline。
引擎注册内置组件，GamePack 可注册扩展组件。
"""

from __future__ import annotations

import logging
from typing import Any, Type

from drama_engine.core.runtime.interactive_session.actions.free_input.components.base import (
    GrowFlowGenerator,
    InteractionModeComponent,
    NarrationStyleComponent,
    PlotConstraintComponent,
    PresentationComponent,
)

logger = logging.getLogger(__name__)


class GrowFlowComponentRegistry:
    """grow_flow 组件注册表。

    职责：
      1. 注册各维度的组件类（name → class 映射）
      2. 根据 DSL generator spec 实例化对应组件
      3. 组装 GrowFlowPipeline
    """

    def __init__(self) -> None:
        """初始化空注册表。"""
        self._narration_styles: dict[str, Type[NarrationStyleComponent]] = {}
        self._interaction_modes: dict[str, Type[InteractionModeComponent]] = {}
        self._constraints: dict[str, Type[PlotConstraintComponent]] = {}
        self._presentations: dict[str, Type[PresentationComponent]] = {}
        self._generators: dict[str, Type[GrowFlowGenerator]] = {}

    # ---- 注册方法 ----

    def register_narration_style(self, name: str, cls: Type[NarrationStyleComponent]) -> None:
        """注册续写风格组件。"""
        assert name, "name 不能为空"
        self._narration_styles[name] = cls
        logger.debug("[GrowFlowRegistry] 注册 narration_style: %s", name)

    def register_interaction_mode(self, name: str, cls: Type[InteractionModeComponent]) -> None:
        """注册互动方式组件。"""
        assert name, "name 不能为空"
        self._interaction_modes[name] = cls
        logger.debug("[GrowFlowRegistry] 注册 interaction_mode: %s", name)

    def register_constraint(self, name: str, cls: Type[PlotConstraintComponent]) -> None:
        """注册剧情约束组件。"""
        assert name, "name 不能为空"
        self._constraints[name] = cls
        logger.debug("[GrowFlowRegistry] 注册 constraint: %s", name)

    def register_presentation(self, name: str, cls: Type[PresentationComponent]) -> None:
        """注册交互展示组件。"""
        assert name, "name 不能为空"
        self._presentations[name] = cls
        logger.debug("[GrowFlowRegistry] 注册 presentation: %s", name)

    def register_generator(self, name: str, cls: Type[GrowFlowGenerator]) -> None:
        """注册生成器。"""
        assert name, "name 不能为空"
        self._generators[name] = cls
        logger.debug("[GrowFlowRegistry] 注册 generator: %s", name)

    # ---- 解析方法 ----

    def resolve_narration_style(self, spec: dict[str, Any]) -> NarrationStyleComponent:
        """根据 spec 解析续写风格组件实例。"""
        name = str(spec.get("narration_style") or "plain_narration")
        cls = self._narration_styles.get(name)
        assert cls is not None, f"未注册的 narration_style: {name}"
        return cls(spec)

    def resolve_interaction_mode(self, spec: dict[str, Any]) -> InteractionModeComponent:
        """根据 spec 解析互动方式组件实例。"""
        name = str(spec.get("interaction_mode") or "branch_choice")
        cls = self._interaction_modes.get(name)
        assert cls is not None, f"未注册的 interaction_mode: {name}"
        return cls(spec)

    def resolve_constraint(self, spec: dict[str, Any]) -> PlotConstraintComponent:
        """根据 spec 解析剧情约束组件实例。"""
        constraint_config = spec.get("constraint") or {}
        if isinstance(constraint_config, str):
            constraint_config = {"type": constraint_config}
        name = str(constraint_config.get("type") or "free")
        cls = self._constraints.get(name)
        assert cls is not None, f"未注册的 constraint: {name}"
        return cls(constraint_config)

    def resolve_presentation(self, spec: dict[str, Any]) -> PresentationComponent:
        """根据 spec 解析交互展示组件实例。"""
        name = str(spec.get("presentation") or "chat_flow")
        cls = self._presentations.get(name)
        assert cls is not None, f"未注册的 presentation: {name}"
        return cls(spec)

    def resolve_generator(self, spec: dict[str, Any]) -> GrowFlowGenerator:
        """根据 spec 解析生成器实例。

        默认走 llm：grow_flow 的本质是调用大模型动态续写剧情，
        builtin(Template) 只是不接 LLM 时的兜底占位（返回"剧情继续向前推进"）。
        LLMGrowFlowGenerator 在无 client 或 dry_run 场景会自动降级到模板，
        所以默认 llm 不会在测试/dry-run 环境报错。
        """
        executor = str(spec.get("executor") or spec.get("evaluator") or "llm")
        # llm / builtin / plugin / http → 映射到注册的 generator
        name = executor if executor in self._generators else "llm"
        cls = self._generators.get(name)
        assert cls is not None, f"未注册的 generator: {name}"
        return cls(spec)

    def resolve_pipeline(self, spec: dict[str, Any]) -> Any:
        """根据 DSL spec 解析完整组件组合，构造 GrowFlowPipeline。

        参数:
            spec: DSL generator 块配置

        返回:
            GrowFlowPipeline 实例
        """
        # 延迟导入避免循环
        from drama_engine.core.runtime.interactive_session.actions.free_input.grow_flow_pipeline import (
            GrowFlowPipeline,
        )

        return GrowFlowPipeline(
            constraint=self.resolve_constraint(spec),
            narration=self.resolve_narration_style(spec),
            interaction=self.resolve_interaction_mode(spec),
            presentation=self.resolve_presentation(spec),
            generator=self.resolve_generator(spec),
        )


def build_default_grow_flow_registry() -> GrowFlowComponentRegistry:
    """构建默认注册表，注册所有内置组件。"""
    from drama_engine.core.runtime.interactive_session.actions.free_input.components.constraints import (
        EndingBoundConstraint,
        FreeConstraint,
        MaxRoundsConstraint,
    )
    from drama_engine.core.runtime.interactive_session.actions.free_input.components.generators import (
        LLMGrowFlowGenerator,
        TemplateGrowFlowGenerator,
    )
    from drama_engine.core.runtime.interactive_session.actions.free_input.components.interaction_modes import (
        BranchChoiceMode,
        ConfirmAdvanceMode,
        FreeInputOnlyMode,
    )
    from drama_engine.core.runtime.interactive_session.actions.free_input.components.narration_styles import (
        DialogueSequenceStyle,
        MixedStyle,
        PlainNarrationStyle,
    )
    from drama_engine.core.runtime.interactive_session.actions.free_input.components.presentations import (
        ChatFlowPresentation,
        CinematicPresentation,
        VisualNovelPresentation,
    )

    registry = GrowFlowComponentRegistry()

    # 续写风格
    registry.register_narration_style("plain_narration", PlainNarrationStyle)
    registry.register_narration_style("dialogue_sequence", DialogueSequenceStyle)
    registry.register_narration_style("mixed", MixedStyle)

    # 互动方式
    registry.register_interaction_mode("branch_choice", BranchChoiceMode)
    registry.register_interaction_mode("free_input_only", FreeInputOnlyMode)
    registry.register_interaction_mode("confirm_advance", ConfirmAdvanceMode)

    # 剧情约束
    registry.register_constraint("free", FreeConstraint)
    registry.register_constraint("max_rounds", MaxRoundsConstraint)
    registry.register_constraint("ending_bound", EndingBoundConstraint)

    # 交互展示
    registry.register_presentation("cinematic", CinematicPresentation)
    registry.register_presentation("chat_flow", ChatFlowPresentation)
    registry.register_presentation("visual_novel", VisualNovelPresentation)

    # 生成器
    registry.register_generator("llm", LLMGrowFlowGenerator)
    registry.register_generator("builtin", TemplateGrowFlowGenerator)

    logger.info("[GrowFlowRegistry] 默认注册表构建完成")
    return registry


__all__ = ["GrowFlowComponentRegistry", "build_default_grow_flow_registry"]
