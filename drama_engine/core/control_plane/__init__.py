"""局内控制角色集合（ControlPlane，架构文档 §11）。

ControlPlane 管理裁判、主持人、导演、编剧、复盘、观众等控制角色。关键边界：
- host / director / writer 不能直接修改权威状态，只能提出 proposal。
- proposal 必须经过 referee / GamePack / RuleSet / PatchValidator 校验。
- 校验通过后才由 EffectExecutor / PatchApplier / StateWriter 应用。
"""

from drama_engine.core.control_plane.roles import (
    ControlProposal,
    ControlRole,
    ProposalVerdict,
    ROLE_KINDS,
)
from drama_engine.core.control_plane.plane import ControlPlane, build_control_plane

__all__ = [
    "ControlProposal",
    "ControlRole",
    "ProposalVerdict",
    "ROLE_KINDS",
    "ControlPlane",
    "build_control_plane",
]
