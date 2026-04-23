"""
team.mailbox — 基于 StorageAdapter 的持久化 Mailbox 客户端。

每个 (team_name, recipient) 对应一个独立的收件箱，
消息以 JSON Lines（file 后端）或数据库行（sqlite/mongo 后端）持久化。
"""

import asyncio
import inspect
from typing import Any, Optional

from loguru import logger

from ccserver.storage.base import StorageAdapter
from .protocol import TeamMessage, deserialize_message


class TeamMailbox:
    """
    团队持久化邮箱客户端。

    封装 StorageAdapter 的 inbox 相关方法，提供发送、读取、标记已读等高层接口。
    兼容同步与异步 StorageAdapter。
    """

    def __init__(self, team_name: str, adapter: Optional[StorageAdapter]):
        self.team_name = team_name
        self.adapter = adapter

    @staticmethod
    def _maybe_await(coro_or_result: Any) -> Any:
        """
        兼容同步与异步 adapter。
        如果返回值是协程对象，则运行事件循环直到完成。
        """
        if inspect.isawaitable(coro_or_result):
            try:
                loop = asyncio.get_running_loop()
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, coro_or_result)
                    return future.result()
            except RuntimeError:
                return asyncio.run(coro_or_result)
        return coro_or_result

    def send(self, message: TeamMessage) -> None:
        """
        向指定接收者 inbox 发送一条消息（同步封装，内部自动桥接 async adapter）。

        Args:
            message: 要发送的 TeamMessage 实例
        """
        if self.adapter is None:
            logger.warning("Mailbox send skipped | no adapter")
            return
        self._maybe_await(
            self.adapter.append_inbox_message(
                self.team_name,
                message.to_agent,
                message.to_dict(),
            )
        )
        logger.debug(
            "Mailbox sent | team={} to={} type={} msg_id={}",
            self.team_name,
            message.to_agent,
            message.msg_type,
            message.msg_id,
        )

    def broadcast(self, message: TeamMessage, recipients: list[str], exclude: Optional[str] = None) -> None:
        """
        向多个接收者广播同一条消息。

        Args:
            message:   要广播的消息模板（to_agent 会被覆盖）
            recipients: 接收者 agent_id 列表
            exclude:   可选，排除某个 agent_id（通常排除发送者自己）
        """
        for recipient in recipients:
            if exclude and recipient == exclude:
                continue
            msg_copy = message.__class__.from_dict(message.to_dict())
            msg_copy.to_agent = recipient
            self.send(msg_copy)

    def fetch_messages(
        self,
        recipient: str,
        unread_only: bool = False,
        limit: int = 100,
    ) -> list[TeamMessage]:
        """
        获取指定接收者的 mailbox 消息列表。

        Args:
            recipient:   接收者 agent_id
            unread_only: True 时只返回未读消息
            limit:       最大返回条数，<=0 表示不限制

        Returns:
            TeamMessage 子类实例列表
        """
        if self.adapter is None:
            return []
        rows = self._maybe_await(
            self.adapter.fetch_inbox_messages(
                self.team_name,
                recipient,
                unread_only=unread_only,
                limit=limit,
            )
        )
        return [deserialize_message(r) for r in rows]

    def mark_read(self, recipient: str, msg_ids: list[str]) -> None:
        """
        将指定消息标记为已读。

        Args:
            recipient: 接收者 agent_id
            msg_ids:   要标记的消息 ID 列表
        """
        if self.adapter is None or not msg_ids:
            return
        self._maybe_await(
            self.adapter.mark_inbox_read(self.team_name, recipient, msg_ids)
        )
        logger.debug(
            "Mailbox marked read | team={} recipient={} count={}",
            self.team_name,
            recipient,
            len(msg_ids),
        )
