"""drama_engine.core.components — 运行时执行组件。

ConditionEvaluator / EffectExecutor / CandidateResolver / ValueResolver 等
对 DSL 声明进行运行时求值和执行的核心组件。
"""

from drama_engine.core.components.conditions import ConditionEvaluator
from drama_engine.core.components.effects import EffectExecutor
from drama_engine.core.components.candidates import CandidateResolver
from drama_engine.core.components.value_resolver import ValueResolver
from drama_engine.core.components.inventory import InventoryManager
from drama_engine.core.components.scoring import ScoreTracker
from drama_engine.core.components.scope_types import make_self_scope_members, make_dynamic_whisper_members

__all__ = [
    "ConditionEvaluator",
    "EffectExecutor",
    "CandidateResolver",
    "ValueResolver",
    "InventoryManager",
    "ScoreTracker",
    "make_self_scope_members",
    "make_dynamic_whisper_members",
]
