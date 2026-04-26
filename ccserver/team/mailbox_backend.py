"""
team.mailbox_backend — Mailbox 持久化后端抽象与实现。

P4 目标：将 TeamMailbox 与具体的 StorageAdapter 解耦，
支持未来替换为 Redis Streams、RabbitMQ 等更高性能的后端。

当前实现：
  - MailboxBackend:     抽象接口（ABC）
  - StorageAdapterBackend: 基于现有 StorageAdapter 的默认实现（向后兼容）

未来扩展：
  - RedisStreamsBackend: 基于 Redis Streams（需要 redis-py 依赖）
"""

from abc import ABC, abstractmethod
from typing import Optional

from loguru import logger


class MailboxBackend(ABC):
    """
    Mailbox 持久化后端抽象接口。

    职责：
      - append:    向指定收件箱追加一条消息
      - fetch:     拉取消息列表
      - mark_read: 标记消息已读

    设计原则：
      - 所有方法均为 async，调用方统一使用 await
      - 不感知 TeamMessage 的业务语义，只处理原始 dict
      - 不同后端实现（文件/SQLite/Redis）对接口透明
    """

    @abstractmethod
    async def append(self, team_name: str, recipient: str, message: dict) -> None:
        """
        向指定收件箱追加一条消息。

        Args:
            team_name: 团队名称
            recipient: 接收者 agent_id
            message:   消息字典（已序列化后的 TeamMessage.to_dict()）
        """

    @abstractmethod
    async def fetch(
        self,
        team_name: str,
        recipient: str,
        unread_only: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        """
        获取指定收件箱的消息列表。

        Args:
            team_name:   团队名称
            recipient:   接收者 agent_id
            unread_only: True 时只返回未读消息
            limit:       最大返回条数，<=0 表示不限制

        Returns:
            消息字典列表（每条为 TeamMessage.to_dict() 的结果）
        """

    @abstractmethod
    async def mark_read(self, team_name: str, recipient: str, msg_ids: list[str]) -> None:
        """
        将指定消息标记为已读。

        Args:
            team_name: 团队名称
            recipient: 接收者 agent_id
            msg_ids:   要标记的消息 ID 列表
        """


class StorageAdapterBackend(MailboxBackend):
    """
    基于 StorageAdapter 的 MailboxBackend 实现（默认/向后兼容）。

    使用现有的 StorageAdapter（file / sqlite / mongo）持久化消息。
    这是 P4 之前的 TeamMailbox 内部实现，现在提取为独立的 Backend 类。

    Args:
        adapter: StorageAdapter 实例，可为 None（此时所有操作静默跳过）
    """

    def __init__(self, adapter: Optional["StorageAdapter"]):
        # 延迟导入避免循环依赖
        from ccserver.storage.base import StorageAdapter
        if adapter is not None and not isinstance(adapter, StorageAdapter):
            raise TypeError(f"adapter must be StorageAdapter or None, got {type(adapter)}")
        self.adapter = adapter

    async def append(self, team_name: str, recipient: str, message: dict) -> None:
        """
        通过 StorageAdapter.append_inbox_message 追加消息。
        """
        if self.adapter is None:
            logger.warning("Mailbox append skipped | no adapter")
            return
        await self.adapter.append_inbox_message(team_name, recipient, message)
        logger.debug(
            "Mailbox appended | team={} recipient={} msg_id={}",
            team_name, recipient, message.get("msg_id"),
        )

    async def fetch(
        self,
        team_name: str,
        recipient: str,
        unread_only: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        """
        通过 StorageAdapter.fetch_inbox_messages 拉取消息。
        """
        if self.adapter is None:
            return []
        rows = await self.adapter.fetch_inbox_messages(
            team_name, recipient, unread_only=unread_only, limit=limit,
        )
        return rows

    async def mark_read(self, team_name: str, recipient: str, msg_ids: list[str]) -> None:
        """
        通过 StorageAdapter.mark_inbox_read 标记已读。
        """
        if self.adapter is None or not msg_ids:
            return
        await self.adapter.mark_inbox_read(team_name, recipient, msg_ids)
        logger.debug(
            "Mailbox marked read | team={} recipient={} count={}",
            team_name, recipient, len(msg_ids),
        )
