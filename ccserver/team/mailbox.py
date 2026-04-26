"""
team.mailbox — 基于 MailboxBackend 的持久化 Mailbox 客户端。

每个 (team_name, recipient) 对应一个独立的收件箱，
消息通过 MailboxBackend 持久化（默认使用 StorageAdapterBackend）。

P4 改动：
  - 从直接依赖 StorageAdapter 改为依赖 MailboxBackend 接口
  - send() / fetch_messages() / mark_read() 改为 async
  - 删除 _maybe_await 同步桥接逻辑
"""

from typing import Optional

from loguru import logger

from .protocol import TeamMessage, deserialize_message
from .mailbox_backend import MailboxBackend, StorageAdapterBackend


class TeamMailbox:
    """
    团队持久化邮箱客户端。

    封装 MailboxBackend 的高层接口，提供发送、读取、标记已读等功能。
    不再直接感知 StorageAdapter，Backend 可插拔替换。

    Args:
        team_name: 团队名称
        backend:   MailboxBackend 实例，或为 StorageAdapter 实例（自动包装）
    """

    def __init__(self, team_name: str, backend: Optional[MailboxBackend] = None):
        self.team_name = team_name
        if backend is None or hasattr(backend, "append_inbox_message"):
            # 向后兼容：传入 StorageAdapter 时自动包装为 StorageAdapterBackend
            self.backend = StorageAdapterBackend(backend)
        else:
            self.backend = backend

    async def send(self, message: TeamMessage) -> None:
        """
        向指定接收者 inbox 发送一条消息（异步）。

        Args:
            message: 要发送的 TeamMessage 实例
        """
        await self.backend.append(
            self.team_name,
            message.to_agent,
            message.to_dict(),
        )
        logger.debug(
            "Mailbox sent | team={} to={} type={} msg_id={}",
            self.team_name,
            message.to_agent,
            message.msg_type,
            message.msg_id,
        )

    async def broadcast(
        self,
        message: TeamMessage,
        recipients: list[str],
        exclude: Optional[str] = None,
    ) -> None:
        """
        向多个接收者广播同一条消息（异步）。

        Args:
            message:    要广播的消息模板（to_agent 会被覆盖）
            recipients: 接收者 agent_id 列表
            exclude:    可选，排除某个 agent_id（通常排除发送者自己）
        """
        for recipient in recipients:
            if exclude and recipient == exclude:
                continue
            msg_copy = message.__class__.from_dict(message.to_dict())
            msg_copy.to_agent = recipient
            await self.send(msg_copy)

    async def fetch_messages(
        self,
        recipient: str,
        unread_only: bool = False,
        limit: int = 100,
    ) -> list[TeamMessage]:
        """
        获取指定接收者的 mailbox 消息列表（异步）。

        Args:
            recipient:   接收者 agent_id
            unread_only: True 时只返回未读消息
            limit:       最大返回条数，<=0 表示不限制

        Returns:
            TeamMessage 子类实例列表
        """
        rows = await self.backend.fetch(
            self.team_name,
            recipient,
            unread_only=unread_only,
            limit=limit,
        )
        return [deserialize_message(r) for r in rows]

    async def mark_read(self, recipient: str, msg_ids: list[str]) -> None:
        """
        将指定消息标记为已读（异步）。

        Args:
            recipient: 接收者 agent_id
            msg_ids:   要标记的消息 ID 列表
        """
        await self.backend.mark_read(self.team_name, recipient, msg_ids)
        logger.debug(
            "Mailbox marked read | team={} recipient={} count={}",
            self.team_name,
            recipient,
            len(msg_ids),
        )
