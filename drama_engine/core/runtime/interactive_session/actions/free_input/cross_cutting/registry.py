"""跨模式组件注册表。

FreeInputComponentRegistry 管理所有跨模式公用组件的注册与解析：
  - InputGuard / OutputGuard
  - Planner
  - ChoiceDesigner
  - AssetResolver

职责：
  1. 注册组件类（name → class 映射）
  2. 根据 DSL spec 实例化对应组件
  3. 支持 GamePack/Plugin 动态注册自定义实现

与 GrowFlowComponentRegistry 互补：
  - GrowFlowComponentRegistry: grow_flow 内部 5 维度（Constraint/NarrationStyle/Generator/InteractionMode/Presentation）
  - FreeInputComponentRegistry: 所有模式共享的横切组件
"""

from __future__ import annotations

import logging
from typing import Any, Type

from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.base import (
    AssetResolver,
    ChoiceDesigner,
    InputGuard,
    OutputGuard,
    Planner,
)

logger = logging.getLogger(__name__)


class FreeInputComponentRegistry:
    """跨模式组件注册表。

    管理 Guard / Planner / ChoiceDesigner / AssetResolver 的注册与解析。
    """

    def __init__(self) -> None:
        """初始化空注册表。"""
        self._input_guards: dict[str, Type[InputGuard]] = {}
        self._output_guards: dict[str, Type[OutputGuard]] = {}
        self._planners: dict[str, Type[Planner]] = {}
        self._choice_designers: dict[str, Type[ChoiceDesigner]] = {}
        self._asset_resolvers: dict[str, Type[AssetResolver]] = {}

    # ════════════════════════════════════════
    # 注册方法
    # ════════════════════════════════════════

    def register_input_guard(self, name: str, cls: Type[InputGuard]) -> None:
        """注册 InputGuard 实现。

        参数:
            name: 组件名称（DSL 中引用的标识符）
            cls: InputGuard 子类
        """
        assert name, "name 不能为空"
        self._input_guards[name] = cls
        logger.debug("[FreeInputComponentRegistry] 注册 InputGuard: %s", name)

    def register_output_guard(self, name: str, cls: Type[OutputGuard]) -> None:
        """注册 OutputGuard 实现。

        参数:
            name: 组件名称
            cls: OutputGuard 子类
        """
        assert name, "name 不能为空"
        self._output_guards[name] = cls
        logger.debug("[FreeInputComponentRegistry] 注册 OutputGuard: %s", name)

    def register_planner(self, name: str, cls: Type[Planner]) -> None:
        """注册 Planner 实现。

        参数:
            name: 组件名称
            cls: Planner 子类
        """
        assert name, "name 不能为空"
        self._planners[name] = cls
        logger.debug("[FreeInputComponentRegistry] 注册 Planner: %s", name)

    def register_choice_designer(self, name: str, cls: Type[ChoiceDesigner]) -> None:
        """注册 ChoiceDesigner 实现。

        参数:
            name: 组件名称
            cls: ChoiceDesigner 子类
        """
        assert name, "name 不能为空"
        self._choice_designers[name] = cls
        logger.debug("[FreeInputComponentRegistry] 注册 ChoiceDesigner: %s", name)

    def register_asset_resolver(self, name: str, cls: Type[AssetResolver]) -> None:
        """注册 AssetResolver 实现。

        参数:
            name: 组件名称
            cls: AssetResolver 子类
        """
        assert name, "name 不能为空"
        self._asset_resolvers[name] = cls
        logger.debug("[FreeInputComponentRegistry] 注册 AssetResolver: %s", name)

    # ════════════════════════════════════════
    # 解析方法
    # ════════════════════════════════════════

    def resolve_input_guards(self, specs: list[dict[str, Any]]) -> list[InputGuard]:
        """根据 DSL 配置解析 InputGuard 实例列表。

        参数:
            specs: guards.input 配置列表
                [{"name": "character_existence", "config": {...}}, ...]

        返回:
            InputGuard 实例列表（按配置顺序）
        """
        guards: list[InputGuard] = []
        for spec in specs:
            name = str(spec.get("name", ""))
            cls = self._input_guards.get(name)
            if cls is None:
                logger.warning("[FreeInputComponentRegistry] 未注册的 InputGuard: %s，跳过", name)
                continue
            config = dict(spec.get("config") or {})
            # 把 executor 也放进 config，方便组件内部判断后端
            config.setdefault("executor", spec.get("executor", "builtin"))
            guards.append(cls(config))
        return guards

    def resolve_output_guards(self, specs: list[dict[str, Any]]) -> list[OutputGuard]:
        """根据 DSL 配置解析 OutputGuard 实例列表。

        参数:
            specs: guards.output 配置列表
                [{"name": "character_voice", "executor": "llm", "config": {...}}, ...]

        返回:
            OutputGuard 实例列表（按配置顺序）
        """
        guards: list[OutputGuard] = []
        for spec in specs:
            name = str(spec.get("name", ""))
            cls = self._output_guards.get(name)
            if cls is None:
                logger.warning("[FreeInputComponentRegistry] 未注册的 OutputGuard: %s，跳过", name)
                continue
            config = dict(spec.get("config") or {})
            config.setdefault("executor", spec.get("executor", "builtin"))
            config.setdefault("on_fail", spec.get("on_fail", "reject"))
            config.setdefault("max_retries", spec.get("max_retries", 2))
            guards.append(cls(config))
        return guards

    def resolve_planner(self, spec: dict[str, Any] | None) -> Planner | None:
        """根据 DSL 配置解析 Planner 实例。

        参数:
            spec: generation.planner 配置
                {"name": "llm_planner", "executor": "llm", "config": {...}}
                None 表示未配置

        返回:
            Planner 实例，或 None（未配置时）
        """
        if not spec:
            return None
        name = str(spec.get("name", ""))
        if not name:
            return None
        cls = self._planners.get(name)
        if cls is None:
            logger.warning("[FreeInputComponentRegistry] 未注册的 Planner: %s", name)
            return None
        config = dict(spec.get("config") or {})
        config.setdefault("executor", spec.get("executor", spec.get("evaluator", "builtin")))
        return cls(config)

    def resolve_choice_designer(self, spec: dict[str, Any] | None) -> ChoiceDesigner | None:
        """根据 DSL 配置解析 ChoiceDesigner 实例。

        参数:
            spec: generation.choice_designer 配置
                {"name": "attitude_triad", "executor": "llm", "config": {...}}
                None 表示未配置

        返回:
            ChoiceDesigner 实例，或 None（未配置时，使用 Generator 产出的 choices）
        """
        if not spec:
            return None
        name = str(spec.get("name", ""))
        if not name:
            return None
        cls = self._choice_designers.get(name)
        if cls is None:
            logger.warning("[FreeInputComponentRegistry] 未注册的 ChoiceDesigner: %s", name)
            return None
        config = dict(spec.get("config") or {})
        config.setdefault("executor", spec.get("executor", spec.get("evaluator", "builtin")))
        return cls(config)

    def resolve_asset_resolver(self, spec: dict[str, Any] | None) -> AssetResolver | None:
        """根据 DSL 配置解析 AssetResolver 实例。

        参数:
            spec: generation.asset_resolver 配置
                {"name": "tag_matcher", "config": {...}}
                None 表示未配置

        返回:
            AssetResolver 实例，或 None（未配置时不匹配资产）
        """
        if not spec:
            return None
        name = str(spec.get("name", ""))
        if not name:
            return None
        cls = self._asset_resolvers.get(name)
        if cls is None:
            logger.warning("[FreeInputComponentRegistry] 未注册的 AssetResolver: %s", name)
            return None
        config = dict(spec.get("config") or {})
        config.setdefault("executor", spec.get("executor", spec.get("evaluator", "builtin")))
        return cls(config)

    # ════════════════════════════════════════
    # 查询方法
    # ════════════════════════════════════════

    def has_input_guard(self, name: str) -> bool:
        """检查是否注册了指定 InputGuard。"""
        return name in self._input_guards

    def has_output_guard(self, name: str) -> bool:
        """检查是否注册了指定 OutputGuard。"""
        return name in self._output_guards

    def has_planner(self, name: str) -> bool:
        """检查是否注册了指定 Planner。"""
        return name in self._planners

    def has_choice_designer(self, name: str) -> bool:
        """检查是否注册了指定 ChoiceDesigner。"""
        return name in self._choice_designers

    def has_asset_resolver(self, name: str) -> bool:
        """检查是否注册了指定 AssetResolver。"""
        return name in self._asset_resolvers

    def list_registered(self) -> dict[str, list[str]]:
        """列出所有已注册组件名称（调试用）。"""
        return {
            "input_guards": list(self._input_guards.keys()),
            "output_guards": list(self._output_guards.keys()),
            "planners": list(self._planners.keys()),
            "choice_designers": list(self._choice_designers.keys()),
            "asset_resolvers": list(self._asset_resolvers.keys()),
        }


def build_default_free_input_component_registry() -> FreeInputComponentRegistry:
    """构建默认注册表，注册所有内置组件。

    返回:
        预注册了内置实现的 FreeInputComponentRegistry
    """
    from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.guards import (
        CharacterExistenceInputGuard,
        ContentSafetyInputGuard,
        OutputCharacterExistenceGuard,
        SchemaConformanceGuard,
    )
    from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.planners import (
        NullPlanner,
        TheClausePlanner,
    )
    from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.choice_designers import (
        PassthroughChoiceDesigner,
    )
    from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.asset_resolvers import (
        TagMatcherAssetResolver,
    )

    registry = FreeInputComponentRegistry()

    # 内置 InputGuard
    registry.register_input_guard("character_existence", CharacterExistenceInputGuard)
    registry.register_input_guard("content_safety", ContentSafetyInputGuard)

    # 内置 OutputGuard
    registry.register_output_guard("output_character_existence", OutputCharacterExistenceGuard)
    registry.register_output_guard("schema_conformance", SchemaConformanceGuard)

    # 内置 Planner
    registry.register_planner("null_planner", NullPlanner)
    registry.register_planner("the_clause_planner", TheClausePlanner)

    # 内置 ChoiceDesigner
    registry.register_choice_designer("passthrough", PassthroughChoiceDesigner)

    # 内置 AssetResolver
    registry.register_asset_resolver("tag_matcher", TagMatcherAssetResolver)

    return registry


__all__ = [
    "FreeInputComponentRegistry",
    "build_default_free_input_component_registry",
]
