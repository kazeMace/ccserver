"""
team.poller — TeamMailboxPoller 实现。

功能：定期从 StorageAdapter 拉取指定 teammate 的未读 mailbox 消息，
      并将其注入到该 teammate Agent 的 inbox 队列中，使其在 _loop() 中被消费。
"""

import asyncio
from loguru import logger

from .mailbox import TeamMailbox


class TeamMailboxPoller:
    """
    Mailbox 消息轮询器。

    每个 teammate Agent 对应一个 Poller 实例，
    生命周期与 BackgroundAgentHandle 绑定。
    """

    def __init__(
        self,
        mailbox: TeamMailbox,
        recipient: str,
        inbox: asyncio.Queue,
        interval: float = 3.0,
    ):
        """
        初始化轮询器。

        Args:
            mailbox:   TeamMailbox 实例
            recipient: 接收者 agent_id（如 researcher@team）
            inbox:     teammate Agent 的 asyncio.Queue（注入目标）
            interval:  轮询间隔（秒），默认 3 秒
        """
        self.mailbox = mailbox
        self.recipient = recipient
        self.inbox = inbox
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._loop_count: int = 0
        self._delivered_count: int = 0
        self._error_count: int = 0

    def start(self) -> None:
        """启动后台轮询协程。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            logger.info(
                "TeamMailboxPoller started | recipient={} interval={}s",
                self.recipient, self.interval
            )

    def stop(self) -> None:
        """停止轮询协程。"""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("TeamMailboxPoller stopped | recipient={}", self.recipient)

    @property
    def is_alive(self) -> bool:
        """返回轮询协程是否仍在运行。"""
        return self._task is not None and not self._task.done()

    async def _run(self) -> None:
        """核心轮询循环。"""
        try:
            while True:
                self._loop_count += 1
                try:
                    msgs = self.mailbox.fetch_messages(
                        self.recipient,
                        unread_only=True,
                        limit=50,
                    )
                except Exception as e:
                    self._error_count += 1
                    logger.error(
                        "Mailbox fetch failed | recipient={} error={}",
                        self.recipient, e
                    )
                    await asyncio.sleep(self.interval)
                    continue

                if msgs:
                    logger.debug(
                        "Poller fetched messages | recipient={} count={}",
                        self.recipient, len(msgs)
                    )
                    msg_ids = []
                    for msg in msgs:
                        payload = {
                            "msg_type": msg.msg_type,
                            "from_agent": msg.from_agent,
                            "to_agent": msg.to_agent,
                            "text": msg.text,
                            "msg_id": msg.msg_id,
                            "timestamp": msg.timestamp,
                            "summary": msg.summary,
                        }
                        # 附加子类特有字段（使用 getattr 安全读取）
                        if msg.msg_type == "new_task":
                            payload["task_id"] = getattr(msg, "task_id", None)
                            payload["task_prompt"] = getattr(msg, "task_prompt", None)
                        elif msg.msg_type == "shutdown_request":
                            payload["reason"] = getattr(msg, "reason", None)
                        elif msg.msg_type == "permission_request":
                            payload["request_id"] = getattr(msg, "request_id", None)
                            payload["tool_name"] = getattr(msg, "tool_name", None)
                            payload["tool_input"] = getattr(msg, "tool_input", {})
                            payload["description"] = getattr(msg, "description", "")
                        elif msg.msg_type == "permission_response":
                            payload["request_id"] = getattr(msg, "request_id", None)
                            payload["approved"] = getattr(msg, "approved", False)
                            payload["feedback"] = getattr(msg, "feedback", None)
                        elif msg.msg_type == "idle_notification":
                            payload["idle_reason"] = getattr(msg, "idle_reason", "available")
                            payload["completed_task_id"] = getattr(msg, "completed_task_id", None)
                            payload["completed_status"] = getattr(msg, "completed_status", None)

                        await self.inbox.put(payload)
                        msg_ids.append(msg.msg_id)
                        self._delivered_count += 1

                    if msg_ids:
                        try:
                            self.mailbox.mark_read(self.recipient, msg_ids)
                        except Exception as e:
                            self._error_count += 1
                            logger.error(
                                "Mailbox mark_read failed | recipient={} error={}",
                                self.recipient, e
                            )

                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            logger.debug("TeamMailboxPoller cancelled | recipient={}", self.recipient)
        except Exception as e:
            self._error_count += 1
            logger.error("TeamMailboxPoller fatal error | recipient={} error={}", self.recipient, e)
