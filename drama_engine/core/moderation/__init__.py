"""内容审查层（GuardRail / OOC 守卫）。

KnowledgeFirewall 管「流入 actor 的信息」（输入过滤，防剧透）；GuardRail 管
「actor 产出的内容」（输出审查，防出圈/离题/泄密）。二者方向相反、职责正交。

GuardRail 在发言写入前判定其是否越界，并按 DSL guardrail.on_violation 声明的
策略处理（block / rewrite / soft_warn / pass_with_flag）。
"""

from drama_engine.core.moderation.guardrail import GuardRail, LLMGuardRail, build_guardrail
from drama_engine.core.moderation.models import GuardDecision, GuardOutcome, GuardRailSpec
from drama_engine.core.moderation.strategies import ViolationStrategy, build_strategy

__all__ = [
    "GuardRail",
    "LLMGuardRail",
    "build_guardrail",
    "GuardRailSpec",
    "GuardDecision",
    "GuardOutcome",
    "ViolationStrategy",
    "build_strategy",
]
