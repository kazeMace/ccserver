"""控制角色集合与提案审批流水线（架构文档 §11）。

ControlPlane 持有本局的控制角色，并实现「提案 → 校验 → 应用」的关键边界：
- submit_proposal：提案角色（host/director/writer）提出提案，不直接改状态。
- review_proposal：referee / PatchValidator 裁定提案是否通过。
- apply_proposal：仅在裁定通过后，把提案交给 applier 应用到权威状态。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from drama_engine.core.control_plane.roles import (
    ControlProposal,
    ControlRole,
    ProposalVerdict,
    ROLE_KINDS,
)

logger = logging.getLogger(__name__)


class ControlPlane:
    """局内控制角色集合与提案审批中心。"""

    def __init__(
        self,
        roles: dict[str, ControlRole],
        validator: Callable[[ControlProposal], ProposalVerdict] | None = None,
        applier: Callable[[ControlProposal], Any] | None = None,
    ) -> None:
        """初始化控制面。

        参数：
          roles     — 角色字典 {kind: ControlRole}。
          validator — 提案裁定函数（缺省时用保守内置裁定）。
          applier   — 提案应用函数（把已通过提案落到权威状态）。
        """
        assert isinstance(roles, dict), "roles 必须是 dict"
        self._roles = roles
        self._validator = validator or self._default_validator
        self._applier = applier
        self._proposals: list[dict[str, Any]] = []

    def has_role(self, kind: str) -> bool:
        """判断某控制角色是否声明。"""
        return kind in self._roles

    def get_role(self, kind: str) -> ControlRole:
        """获取某控制角色。"""
        assert kind in self._roles, f"控制角色未声明: {kind}"
        return self._roles[kind]

    def submit_proposal(self, proposal: ControlProposal) -> ProposalVerdict:
        """提案角色提交提案：先记录，再裁定，通过则应用。

        返回裁定结果。提案与裁定都会记入审批历史，便于回放/复盘。
        """
        assert isinstance(proposal, ControlProposal), "proposal 必须是 ControlProposal"
        assert proposal.role in self._roles, f"控制角色未声明: {proposal.role}"
        # 权威角色（referee）不通过 proposal 流程，它是裁定方。
        assert not self.get_role(proposal.role).can_author_authority, (
            "referee 是裁定方，不通过 submit_proposal 提案"
        )
        verdict = self._validator(proposal)
        record = {"proposal": proposal.to_dict(), "verdict": verdict.to_dict(), "applied": False}
        if verdict.approved and self._applier is not None:
            self._applier(proposal)
            record["applied"] = True
        self._proposals.append(record)
        logger.info(
            "[ControlPlane] proposal role=%s kind=%s approved=%s applied=%s",
            proposal.role,
            proposal.kind,
            verdict.approved,
            record["applied"],
        )
        return verdict

    def proposals(self) -> list[dict[str, Any]]:
        """返回提案审批历史。"""
        return list(self._proposals)

    def _default_validator(self, proposal: ControlProposal) -> ProposalVerdict:
        """保守内置裁定：只放行结构完整的已知提案类型。

        真实游戏应注入基于 referee/GamePack/PatchValidator 的裁定函数。
        """
        known = {"patch", "effect", "announcement", "scene_beat"}
        if proposal.kind not in known:
            return ProposalVerdict(False, f"未知提案类型: {proposal.kind}")
        if not isinstance(proposal.payload, dict) or not proposal.payload:
            return ProposalVerdict(False, "提案 payload 为空")
        return ProposalVerdict(True, "内置保守裁定通过")

    def to_dict(self) -> dict[str, Any]:
        """返回控制面声明的可序列化视图。"""
        return {kind: role.to_dict() for kind, role in self._roles.items()}


def build_control_plane(
    spec: dict[str, Any] | None,
    validator: Callable[[ControlProposal], ProposalVerdict] | None = None,
    applier: Callable[[ControlProposal], Any] | None = None,
) -> ControlPlane:
    """从 DSL control_plane 声明构建 ControlPlane。

    spec 形如 {referee: {type: system}, writer: {type: agent, agent_id: narrator}}。
    未声明任何角色时返回空控制面（纯玩法脚本无需控制角色）。
    """
    roles: dict[str, ControlRole] = {}
    for kind, role_spec in (spec or {}).items():
        if kind not in ROLE_KINDS:
            continue
        if isinstance(role_spec, str):
            roles[kind] = ControlRole(kind=kind, actor_type=role_spec)
        elif isinstance(role_spec, dict):
            actor_type = str(role_spec.get("type") or role_spec.get("actor_type") or "system")
            config = {k: v for k, v in role_spec.items() if k not in {"type", "actor_type"}}
            roles[kind] = ControlRole(kind=kind, actor_type=actor_type, config=config)
    return ControlPlane(roles=roles, validator=validator, applier=applier)


__all__ = ["ControlPlane", "build_control_plane"]
