"""
team.protocol — Agent Team 消息协议定义。

定义所有通过 Mailbox 传递的标准化消息格式，
包括聊天、idle 通知、任务分配、权限请求、关闭请求等类型。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Optional, Literal
import uuid


class MsgType(StrEnum):
    """团队消息类型枚举。继承 StrEnum，可直接与字符串比较和序列化。"""
    CHAT               = "chat"
    IDLE_NOTIFICATION  = "idle_notification"
    NEW_TASK           = "new_task"
    SHUTDOWN_REQUEST   = "shutdown_request"
    PERMISSION_REQUEST = "permission_request"
    PERMISSION_RESPONSE = "permission_response"
    STATUS_REQUEST     = "status_request"   # 由 _poll_agent_progress 注入，非 Mailbox 消息


@dataclass
class TeamMessage:
    """
    所有团队消息的统一基类。

    Attributes:
        msg_id:      消息唯一标识，默认生成 uuid4
        msg_type:    消息类型字符串（由子类覆盖）
        from_agent:  发送者 agent_id（格式 name@teamName）
        to_agent:    接收者 agent_id；"*" 表示广播
        text:        消息正文，人类可读
        timestamp:   ISO 格式 UTC 时间戳
        read:        是否已读标记
        summary:     可选摘要，供 UI 预览
    """

    msg_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    msg_type: str = MsgType.CHAT
    from_agent: str = ""
    to_agent: str = ""
    text: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    read: bool = False
    summary: Optional[str] = None

    def to_dict(self) -> dict:
        """将消息对象序列化为字典，用于持久化存储。"""
        return {
            "msg_id": self.msg_id,
            "msg_type": self.msg_type,
            "from": self.from_agent,
            "to": self.to_agent,
            "text": self.text,
            "timestamp": self.timestamp,
            "read": self.read,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TeamMessage":
        """从字典反序列化消息对象。"""
        return cls(
            msg_id=data.get("msg_id", ""),
            msg_type=data.get("msg_type", "chat"),
            from_agent=data.get("from", ""),
            to_agent=data.get("to", ""),
            text=data.get("text", ""),
            timestamp=data.get("timestamp", ""),
            read=data.get("read", False),
            summary=data.get("summary"),
        )


@dataclass
class ChatMessage(TeamMessage):
    """普通团队聊天消息。"""
    msg_type: str = MsgType.CHAT


@dataclass
class IdleNotificationMessage(TeamMessage):
    """
    Teammate 进入 idle 状态时发送的通知。

    idle_reason: 进入 idle 的原因
    completed_task_id: 刚刚完成的任务 ID（如有）
    completed_status: 任务完成状态（resolved / blocked / failed）
    """

    msg_type: str = MsgType.IDLE_NOTIFICATION
    idle_reason: Literal["available", "interrupted", "failed"] = "available"
    completed_task_id: Optional[str] = None
    completed_status: Optional[Literal["resolved", "blocked", "failed"]] = None

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["idle_reason"] = self.idle_reason
        base["completed_task_id"] = self.completed_task_id
        base["completed_status"] = self.completed_status
        return base

    @classmethod
    def from_dict(cls, data: dict) -> "IdleNotificationMessage":
        base = TeamMessage.from_dict(data)
        return cls(
            msg_id=base.msg_id,
            msg_type=base.msg_type,
            from_agent=base.from_agent,
            to_agent=base.to_agent,
            text=base.text,
            timestamp=base.timestamp,
            read=base.read,
            summary=base.summary,
            idle_reason=data.get("idle_reason", "available"),
            completed_task_id=data.get("completed_task_id"),
            completed_status=data.get("completed_status"),
        )


@dataclass
class NewTaskMessage(TeamMessage):
    """向 idle teammate 分配新任务的消息。"""

    msg_type: str = MsgType.NEW_TASK
    task_id: str = ""
    task_prompt: str = ""

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["task_id"] = self.task_id
        base["task_prompt"] = self.task_prompt
        return base

    @classmethod
    def from_dict(cls, data: dict) -> "NewTaskMessage":
        base = TeamMessage.from_dict(data)
        return cls(
            msg_id=base.msg_id,
            msg_type=base.msg_type,
            from_agent=base.from_agent,
            to_agent=base.to_agent,
            text=base.text,
            timestamp=base.timestamp,
            read=base.read,
            summary=base.summary,
            task_id=data.get("task_id", ""),
            task_prompt=data.get("task_prompt", ""),
        )


@dataclass
class ShutdownRequestMessage(TeamMessage):
    """Team Lead 请求 teammate 优雅退出。"""

    msg_type: str = MsgType.SHUTDOWN_REQUEST
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["reason"] = self.reason
        return base

    @classmethod
    def from_dict(cls, data: dict) -> "ShutdownRequestMessage":
        base = TeamMessage.from_dict(data)
        return cls(
            msg_id=base.msg_id,
            msg_type=base.msg_type,
            from_agent=base.from_agent,
            to_agent=base.to_agent,
            text=base.text,
            timestamp=base.timestamp,
            read=base.read,
            summary=base.summary,
            reason=data.get("reason"),
        )


@dataclass
class PermissionRequestMessage(TeamMessage):
    """
    Worker Agent 向 Team Lead 发送的权限审批请求。

    request_id:  本次请求唯一标识，用于响应时匹配
    tool_name:   请求使用的工具名称
    tool_input:  工具的输入参数
    description: 人类可读描述
    """

    msg_type: str = MsgType.PERMISSION_REQUEST
    request_id: str = ""
    tool_name: str = ""
    tool_input: dict = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["request_id"] = self.request_id
        base["tool_name"] = self.tool_name
        base["tool_input"] = self.tool_input
        base["description"] = self.description
        return base

    @classmethod
    def from_dict(cls, data: dict) -> "PermissionRequestMessage":
        base = TeamMessage.from_dict(data)
        return cls(
            msg_id=base.msg_id,
            msg_type=base.msg_type,
            from_agent=base.from_agent,
            to_agent=base.to_agent,
            text=base.text,
            timestamp=base.timestamp,
            read=base.read,
            summary=base.summary,
            request_id=data.get("request_id", ""),
            tool_name=data.get("tool_name", ""),
            tool_input=data.get("tool_input", {}),
            description=data.get("description", ""),
        )


@dataclass
class PermissionResponseMessage(TeamMessage):
    """Team Lead 对权限审批请求的响应。"""

    msg_type: str = MsgType.PERMISSION_RESPONSE
    request_id: str = ""
    approved: bool = False
    feedback: Optional[str] = None

    def to_dict(self) -> dict:
        base = super().to_dict()
        base["request_id"] = self.request_id
        base["approved"] = self.approved
        base["feedback"] = self.feedback
        return base

    @classmethod
    def from_dict(cls, data: dict) -> "PermissionResponseMessage":
        base = TeamMessage.from_dict(data)
        return cls(
            msg_id=base.msg_id,
            msg_type=base.msg_type,
            from_agent=base.from_agent,
            to_agent=base.to_agent,
            text=base.text,
            timestamp=base.timestamp,
            read=base.read,
            summary=base.summary,
            request_id=data.get("request_id", ""),
            approved=data.get("approved", False),
            feedback=data.get("feedback"),
        )


# 消息类型到反序列化函数的映射
_MESSAGE_DESERIALIZERS: dict[str, type] = {
    "chat": ChatMessage,
    "idle_notification": IdleNotificationMessage,
    "new_task": NewTaskMessage,
    "shutdown_request": ShutdownRequestMessage,
    "permission_request": PermissionRequestMessage,
    "permission_response": PermissionResponseMessage,
}


def deserialize_message(data: dict) -> TeamMessage:
    """
    根据 msg_type 自动路由到对应的消息子类进行反序列化。

    Args:
        data: 消息字典

    Returns:
        对应子类的 TeamMessage 实例；未知类型时回退到基类 TeamMessage
    """
    msg_type = data.get("msg_type", "chat")
    cls = _MESSAGE_DESERIALIZERS.get(msg_type, TeamMessage)
    return cls.from_dict(data)
