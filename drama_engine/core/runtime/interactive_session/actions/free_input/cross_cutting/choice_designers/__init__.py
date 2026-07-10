"""内置选项设计器。"""

from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.choice_designers.llm_fallback import (
    LLMFallbackChoiceDesigner,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.choice_designers.passthrough import (
    PassthroughChoiceDesigner,
)

__all__ = ["LLMFallbackChoiceDesigner", "PassthroughChoiceDesigner"]
