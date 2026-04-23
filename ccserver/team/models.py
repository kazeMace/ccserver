"""
team.models — Agent Team 数据模型。

定义团队成员角色、状态、成员信息及团队本体，
并提供与字典之间的序列化/反序列化方法。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class TeamMemberRole(Enum):
    """
    团队成员角色。

    LEAD     — 队长，负责协调、分配任务、审批权限
    TEAMMATE — 队员，执行具体任务
    """

    LEAD = "lead"
    TEAMMATE = "teammate"


class TeamMemberState(Enum):
    """
    团队成员运行状态。

    IDLE     — 空闲，等待新任务
    BUSY     — 正在执行任务
    OFFLINE  — 离线或未启动
    ERROR    — 出错
    """

    IDLE = "idle"
    BUSY = "busy"
    OFFLINE = "offline"
    ERROR = "error"


@dataclass
class TeamMember:
    """
    单个团队成员的数据表示。

    Attributes:
        agent_id: 确定性全局唯一 ID，格式为 name@teamName
        name:     agent 名称（不含团队后缀）
        role:     角色（LEAD / TEAMMATE）
        state:    当前运行状态
        color:    UI 颜色（可选，如 "#FF6F00"）
        joined_at: 加入团队的时间（UTC）
        metadata:  扩展字段字典
    """

    agent_id: str
    name: str
    role: TeamMemberRole = TeamMemberRole.TEAMMATE
    state: TeamMemberState = TeamMemberState.IDLE
    color: str | None = None
    joined_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """将成员对象序列化为字典。"""
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role.value,
            "state": self.state.value,
            "color": self.color,
            "joined_at": self.joined_at.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TeamMember":
        """从字典反序列化成员对象。"""
        return cls(
            agent_id=data["agent_id"],
            name=data["name"],
            role=TeamMemberRole(data["role"]),
            state=TeamMemberState(data["state"]),
            color=data.get("color"),
            joined_at=datetime.fromisoformat(data["joined_at"]),
            metadata=data.get("metadata", {}),
        )


@dataclass
class Team:
    """
    Agent 团队的数据表示。

    Attributes:
        name:          团队名称，全局唯一标识
        lead_id:       队长 agent_id（None 表示尚未指定队长）
        members:       成员字典，key 为 agent_id，value 为 TeamMember
        created_at:    团队创建时间（UTC）
        allowed_paths: 团队共享的允许路径列表（用于权限控制）
        metadata:      扩展字段字典
    """

    name: str
    lead_id: str | None = None
    members: dict[str, TeamMember] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    allowed_paths: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """将团队对象序列化为字典。"""
        return {
            "name": self.name,
            "lead_id": self.lead_id,
            "members": {agent_id: m.to_dict() for agent_id, m in self.members.items()},
            "created_at": self.created_at.isoformat(),
            "allowed_paths": self.allowed_paths,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Team":
        """从字典反序列化团队对象。"""
        members = {}
        for agent_id, m_data in data.get("members", {}).items():
            members[agent_id] = TeamMember.from_dict(m_data)
        return cls(
            name=data["name"],
            lead_id=data.get("lead_id"),
            members=members,
            created_at=datetime.fromisoformat(data["created_at"]),
            allowed_paths=data.get("allowed_paths", []),
            metadata=data.get("metadata", {}),
        )
