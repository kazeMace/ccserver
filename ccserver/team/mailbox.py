"""
team.mailbox — 基于 MailboxBackend 的持久化 Mailbox 客户端。

每个 (team_name, recipient) 对应一个独立的收件箱，
消息通过 MailboxBackend 持久化（默认使用 StorageAdapterBackend）。

阶段 4 改动：
  - __init__ 新增可选 event_bus / session_id 参数
  - send() 末尾向 EventBus 发 MAILBOX_ARRIVED 通知（唤醒 Poller，消除 3s 轮询延迟）
"""

from typing import Optional, TYPE_CHECKING

from loguru import logger

from .protocol import TeamMessage, deserialize_message
from .mailbox_backend import MailboxBackend, StorageAdapterBackend

if TYPE_CHECKING:
    from ccserver.event_bus import EventBus


class TeamMailbox:
    """
    团队持久化邮箱客户端。

    封装 MailboxBackend 的高层接口，提供发送、读取、标记已读等功能。
    不再直接感知 StorageAdapter，Backend 可插拔替换。

    Args:
        team_name:  团队名称
        backend:    MailboxBackend 实例，或为 StorageAdapter 实例（自动包装）
        event_bus:  可选，Session 级 EventBus。提供时 send() 会额外发通知，
                    让 TeamMailboxPoller 立即唤醒（消除 3s 轮询延迟）
        session_id: 所属 Session ID，与 event_bus 一起用于发通知
    """

    def __init__(
        self,
        team_name: str,
        backend: Optional[MailboxBackend] = None,
        event_bus: Optional["EventBus"] = None,
        session_id: str = "",
    ):
        self.team_name = team_name
        if backend is None or hasattr(backend, "append_inbox_message"):
            # 向后兼容：传入 StorageAdapter 时自动包装为 StorageAdapterBackend
            self.backend = StorageAdapterBackend(backend)
        else:
            self.backend = backend
        self._event_bus = event_bus
        self._session_id = session_id

    async def send(self, message: TeamMessage) -> None:
        """
        向指定接收者 inbox 发送一条消息，并发 EventBus 通知加速唤醒 Poller。

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
        # 向 EventBus 发通知，让 Poller 从 30s 等待中立即唤醒
        # visibility="hidden" 确保不触发 Processor 推送给用户
        if self._event_bus is not None:
            from ccserver.event_bus import AgentEvent, EventType
            try:
                await self._event_bus.publish(AgentEvent(
                    type=EventType.MAILBOX_ARRIVED,
                    agent_id="mailbox",
                    session_id=self._session_id,
                    payload={"recipient": message.to_agent, "msg_id": message.msg_id},
                    to_agent=message.to_agent,
                    visibility="hidden",
                ))
            except Exception as e:
                # 通知失败不影响主流程（Poller 有 30s 兜底轮询）
                logger.debug("Mailbox ARRIVED notify failed | err={}", e)


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
