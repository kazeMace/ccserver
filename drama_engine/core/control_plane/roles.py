"""控制角色与提案模型（架构文档 §11）。

控制角色可为 system / human / agent / plugin / none。角色分两类：
- 权威角色（referee）：可以裁定 proposal 是否通过。
- 提案角色（host / director / writer / recap / audience）：只能提出 proposal，
  不能直接改权威状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 六种控制角色（架构文档 §11）。
ROLE_KINDS = ("referee", "host", "director", "writer", "recap", "audience")

# 角色实现方式。
ACTOR_TYPES = ("system", "human", "agent", "plugin", "none")


@dataclass(slots=True)
class ControlProposal:
    """控制角色提出的一个提案。

    提案不直接改状态；它描述「想做什么」，交由 referee/validator 裁定后再 apply。

    字段：
      role     — 提出提案的角色（host/director/writer 等）。
      kind     — 提案类型，例如 patch / effect / announcement / scene_beat。
      payload  — 提案内容（如 flow_patch、effect 列表、公告文本）。
      reason   — 提案理由，便于回放与复盘。
    """

    role: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def __post_init__(self) -> None:
        assert self.role in ROLE_KINDS, f"未知控制角色: {self.role}"
        assert self.kind, "proposal.kind 不能为空"

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化字典。"""
        return {
            "role": self.role,
            "kind": self.kind,
            "payload": dict(self.payload),
            "reason": self.reason,
        }


@dataclass(slots=True)
class ProposalVerdict:
    """referee/validator 对提案的裁定结果。"""

    approved: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化字典。"""
        return {"approved": self.approved, "reason": self.reason}


@dataclass(slots=True)
class ControlRole:
    """一个控制角色的声明。

    字段：
      kind       — referee/host/director/writer/recap/audience。
      actor_type — system/human/agent/plugin/none。
      config     — 角色配置（如 agent_id、plugin name）。
    """

    kind: str
    actor_type: str = "system"
    config: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        assert self.kind in ROLE_KINDS, f"未知控制角色: {self.kind}"
        assert self.actor_type in ACTOR_TYPES, f"未知 actor_type: {self.actor_type}"

    @property
    def can_author_authority(self) -> bool:
        """是否为权威角色（只有 referee 可以直接裁定/结算）。"""
        return self.kind == "referee"

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化字典。"""
        return {"kind": self.kind, "actor_type": self.actor_type, "config": dict(self.config)}


__all__ = ["ControlProposal", "ProposalVerdict", "ControlRole", "ROLE_KINDS", "ACTOR_TYPES"]
