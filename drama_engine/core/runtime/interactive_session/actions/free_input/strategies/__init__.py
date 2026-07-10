"""自由输入策略模块。

导出所有策略基类和内置实现。
"""

from drama_engine.core.runtime.interactive_session.actions.free_input.strategies.base import (
    FreeInputStrategy,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.strategies.choice_mapping import (
    DifflibChoiceMappingStrategy,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.strategies.content_generation import (
    FixedTextContentGenerationStrategy,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.strategies.ending_selection import (
    ConditionEndingSelectionStrategy,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.strategies.flow_patch_generation import (
    TemplateFlowPatchGenerationStrategy,
)

__all__ = [
    "FreeInputStrategy",
    "DifflibChoiceMappingStrategy",
    "FixedTextContentGenerationStrategy",
    "ConditionEndingSelectionStrategy",
    "TemplateFlowPatchGenerationStrategy",
]
