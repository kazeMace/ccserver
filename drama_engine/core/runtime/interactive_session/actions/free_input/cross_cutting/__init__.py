"""跨模式组件包。

提供 Free-Input Pipeline 中所有跨模式公用组件的基类、数据结构和注册表。
"""

from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.base import (
    AssetResolver,
    ChoiceDesigner,
    InputGuard,
    OutputGuard,
    Planner,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.models import (
    AssetMatch,
    GenerationInput,
    GuardResult,
    PlanResult,
)

__all__ = [
    "AssetMatch",
    "AssetResolver",
    "ChoiceDesigner",
    "GenerationInput",
    "GuardResult",
    "InputGuard",
    "OutputGuard",
    "PlanResult",
    "Planner",
]
