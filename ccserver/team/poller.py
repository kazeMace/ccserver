"""
team.poller — TeamMailboxPoller 实现。

功能：从 TeamMailbox 拉取指定 teammate 的未读消息，
      注入到该 teammate Agent 的 inbox 队列中，使其在 _loop() 中被消费。

阶段 4 改动：
  - 新增 EventBus 订阅驱动模式：有 MAILBOX_ARRIVED 通知时立即拉取，
    无通知时最多等待 30 秒（兜底轮询）。
  - _delivered_ids 改为带 TTL 的 _TimedSet，消除长期运行 teammate 的内存无界增长。
  - 向后兼容：无 event_bus 时退回原始 3 秒轮询模式。
"""

import asyncio
import time
from collections import OrderedDict
from typing import Optional, TYPE_CHECKING

from loguru import logger

from .mailbox import TeamMailbox

if TYPE_CHECKING:
    from ccserver.event_bus import EventBus


# ── _TimedSet：带 TTL 的去重集合 ─────────────────────────────────────────────

class _TimedSet:
    """
    带过期清理的消息 ID 去重集合。

    与普通 set() 相比，能自动清除超过 TTL 的旧条目，
    避免长期运行的 Poller 中 _delivered_ids 无界增长。

    Args:
        ttl_seconds: 条目保留时长（秒），默认 1 小时
    """

    def __init__(self, ttl_seconds: float = 3600):
        # OrderedDict 保持插入顺序，便于按时间顺序清除旧条目
        self._data: OrderedDict[str, float] = OrderedDict()
        self._ttl = ttl_seconds

    def add(self, key: str) -> None:
        """添加条目并触发过期清理。"""
        self._data[key] = time.monotonic()
        self._purge()

    def __contains__(self, key: str) -> bool:
        """检查条目是否存在（同时触发过期清理）。"""
        self._purge()
        return key in self._data

    def _purge(self) -> None:
        """移除所有超过 TTL 的旧条目（按插入顺序，遇到未过期条目即停止）。"""
        cutoff = time.monotonic() - self._ttl
        while self._data:
            oldest_key, oldest_ts = next(iter(self._data.items()))
            if oldest_ts >= cutoff:
                break
            self._data.popitem(last=False)


# ── TeamMailboxPoller ─────────────────────────────────────────────────────────

class TeamMailboxPoller:
    """
    Mailbox 消息轮询器。

    每个 teammate Agent 对应一个 Poller 实例，
    生命周期与 BackgroundAgentHandle 绑定。

    运行模式：
      - 有 event_bus：订阅 MAILBOX_ARRIVED 通知，立即拉取（< 100ms 延迟）
        + 30s 兜底轮询（防止断线丢通知）
      - 无 event_bus：原始 interval 秒轮询（向后兼容）
    """

    def __init__(
        self,
        mailbox: TeamMailbox,
        recipient: str,
        inbox: asyncio.Queue,
        interval: float = 3.0,
        event_bus: Optional["EventBus"] = None,
    ):
        """
        初始化轮询器。

        Args:
            mailbox:   TeamMailbox 实例
            recipient: 接收者 agent_id（如 researcher@team）
            inbox:     teammate Agent 的 asyncio.Queue（注入目标）
            interval:  无 event_bus 时的轮询间隔（秒），默认 3 秒
            event_bus: 可选，Session 级 EventBus。提供时切换为事件驱动模式
        """
        self.mailbox = mailbox
        self.recipient = recipient
        self.inbox = inbox
        self.interval = interval
        self._event_bus = event_bus
        self._task: asyncio.Task | None = None
        self._loop_count: int = 0
        self._delivered_count: int = 0
        self._error_count: int = 0
        # 带 TTL 的去重集合（替代原始 set()，避免无界增长）
        self._delivered_ids: _TimedSet = _TimedSet(ttl_seconds=3600)

    def start(self) -> None:
        """启动后台协程（幂等：已在运行时不重复启动）。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            mode = "event-driven" if self._event_bus else f"polling({self.interval}s)"
            logger.info(
                "TeamMailboxPoller started | recipient={} mode={}",
                self.recipient, mode,
            )

    def stop(self) -> None:
        """停止后台协程。"""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("TeamMailboxPoller stopped | recipient={}", self.recipient)

    @property
    def is_alive(self) -> bool:
        """返回后台协程是否仍在运行。"""
        return self._task is not None and not self._task.done()

    async def _run(self) -> None:
        """
        核心循环入口：根据是否有 event_bus 选择运行模式。
        """
        if self._event_bus is not None:
            await self._run_event_driven()
        else:
            await self._run_polling_fallback()

    async def _run_event_driven(self) -> None:
        """
        事件驱动模式：订阅 MAILBOX_ARRIVED 通知，有通知立即拉取，
        无通知时最多等待 30 秒（兜底轮询，防止断线丢通知）。
        """
        from ccserver.event_bus import EventType
        filter_fn = lambda e: (
            e.type == EventType.MAILBOX_ARRIVED
            and e.to_agent == self.recipient
        )
        sub_id = f"poller_{self.recipient}"
        try:
            async with self._event_bus.subscribe(sub_id, filter_fn=filter_fn) as sub:
                while True:
                    self._loop_count += 1
                    try:
                        # 等待通知，最多 30s（兜底轮询）
                        await sub.get(timeout=30.0)
                    except asyncio.CancelledError:
                        break
                    # 收到通知（或超时兜底），立即拉取消息
                    await self._fetch_and_deliver()
        except asyncio.CancelledError:
            logger.debug("TeamMailboxPoller (event-driven) cancelled | recipient={}", self.recipient)
        except Exception as e:
            self._error_count += 1
            logger.error(
                "TeamMailboxPoller (event-driven) fatal error | recipient={} error={}",
                self.recipient, e,
            )

    async def _run_polling_fallback(self) -> None:
        """
        原始轮询模式（向后兼容：无 event_bus 时使用）。
        每隔 interval 秒拉取一次消息。
        """
        try:
            while True:
                self._loop_count += 1
                await self._fetch_and_deliver()
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            logger.debug("TeamMailboxPoller (polling) cancelled | recipient={}", self.recipient)
        except Exception as e:
            self._error_count += 1
            logger.error(
                "TeamMailboxPoller (polling) fatal error | recipient={} error={}",
                self.recipient, e,
            )

    async def _fetch_and_deliver(self) -> None:
        """
        拉取未读消息并注入到 inbox 队列，同时标记已读。
        """
        try:
            msgs = await self.mailbox.fetch_messages(
                self.recipient,
                unread_only=True,
                limit=50,
            )
        except Exception as e:
            self._error_count += 1
            logger.error(
                "Mailbox fetch failed | recipient={} error={}",
                self.recipient, e,
            )
            return

        if not msgs:
            return

        logger.debug(
            "Poller fetched messages | recipient={} count={}",
            self.recipient, len(msgs),
        )

        msg_ids = []
        for msg in msgs:
            # 去重：已投递过的消息跳过
            if msg.msg_id in self._delivered_ids:
                logger.debug(
                    "Poller dedup skipped | recipient={} msg_id={}",
                    self.recipient, msg.msg_id,
                )
                msg_ids.append(msg.msg_id)
                continue

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
            # P1-2：permission_request / permission_response 已废弃 Mailbox 路径，
            # 权限审批统一走 EventBus PERMISSION_REQ，此处不再提取这两种消息的字段。
            elif msg.msg_type == "idle_notification":
                payload["idle_reason"] = getattr(msg, "idle_reason", "available")
                payload["completed_task_id"] = getattr(msg, "completed_task_id", None)
                payload["completed_status"] = getattr(msg, "completed_status", None)

            await self.inbox.put(payload)
            self._delivered_ids.add(msg.msg_id)
            msg_ids.append(msg.msg_id)
            self._delivered_count += 1

        if msg_ids:
            try:
                await self.mailbox.mark_read(self.recipient, msg_ids)
            except Exception as e:
                self._error_count += 1
                logger.error(
                    "Mailbox mark_read failed | recipient={} error={}",
                    self.recipient, e,
                )
