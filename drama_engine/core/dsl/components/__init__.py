# drama_engine/components/__init__.py
"""drama_engine.components — 组件库，供 YAML 编译器使用。"""

from .conditions import ConditionEvaluator
from .effects import EffectExecutor
from .candidates import CandidateResolver
from .value_resolver import ValueResolver
from .inventory import InventoryManager
from .scoring import ScoreTracker
from .scope_types import make_self_scope_members, make_dynamic_whisper_members

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
