"""Free-input module for controller actions.

导出自由输入执行器、子组件、策略注册表和适配器。
"""

from drama_engine.core.runtime.interactive_session.actions.free_input.adapters import (
    HttpStrategyAdapter,
    LLMStrategyAdapter,
    PluginStrategyAdapter,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.content_generator import (
    ContentGenerator,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.executor import (
    FreeInputExecutor,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.flow_grower import (
    FlowGrower,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.grow_flow_pipeline import (
    GrowFlowPipeline,
    GrowFlowStrategy,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.grow_flow_registry import (
    GrowFlowComponentRegistry,
    build_default_grow_flow_registry,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.grow_state import (
    GrowFlowState,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.mapper_executor import (
    MapperExecutor,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.registry import (
    FreeInputStrategyRegistry,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.strategies import (
    ConditionEndingSelectionStrategy,
    DifflibChoiceMappingStrategy,
    FixedTextContentGenerationStrategy,
    FreeInputStrategy,
    TemplateFlowPatchGenerationStrategy,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.strategy_resolver import (
    StrategyResolver,
)

__all__ = [
    # 执行器（主协调器）
    "FreeInputExecutor",
    # 子组件
    "StrategyResolver",
    "MapperExecutor",
    "ContentGenerator",
    "FlowGrower",
    # 策略基类
    "FreeInputStrategy",
    # 策略注册表
    "FreeInputStrategyRegistry",
    # 内置策略
    "DifflibChoiceMappingStrategy",
    "FixedTextContentGenerationStrategy",
    "ConditionEndingSelectionStrategy",
    "TemplateFlowPatchGenerationStrategy",
    # grow_flow
    "GrowFlowStrategy",
    "GrowFlowPipeline",
    "GrowFlowComponentRegistry",
    "build_default_grow_flow_registry",
    "GrowFlowState",
    # 适配器
    "PluginStrategyAdapter",
    "LLMStrategyAdapter",
    "HttpStrategyAdapter",
]
