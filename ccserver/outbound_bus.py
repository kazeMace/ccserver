"""
outbound_bus — Session 级出站事件总线。

设计目的
────────
  ChannelGateway 不应该直接调用 ChannelAdapter.send_message()。
  出站回复应该通过 OutboundBus 广播，各 Adapter / Emitter 按需订阅，
  自己决定如何发送（流式？合并？编辑？）。

与 EventBus 的区别
────────────────
  EventBus      : Agent 内部事件（token、tool_start、progress、DONE、ERROR）
                  → 任意数量的观察者独立订阅，互不影响
                  → 用于 SSE/WS 实时流式、Recorder 记录等

  OutboundBus   : 出站回复事件（reply、typing）
                  → 只发给"关心该 session"的 channel adapter
                  → 用于外部平台（飞书/钉钉/QQ）发送最终回复

为什么不用 EventBus 代替？
────────────────────────
  1. EventBus 广播 DONE 事件给所有订阅者（SSE、Recorder、父 Agent...）
     OutboundBus 只发给"负责回复"的 channel adapter
  2. OutboundBus 的事件语义是"回复"，不是"Agent 内部状态"
  3. 未来可以扩展为 OutboundBus 支持编辑同一条消息（流式卡片），
     而 EventBus 不支持这种语义

典型用法
────────
  # 1. 创建 OutboundBus（全局单例，由 ChannelGateway 持有）
  outbound_bus = OutboundBus()

  # 2. Adapter 订阅特定 session 的出站事件
  outbound_bus.subscribe(session_id, my_handler)

  # 3. ChannelGateway 发布回复
  await outbound_bus.publish(OutboundEvent(
      session_id=session_id,
      text="Hello!",
      is_final=True,
  ))

  # 4. 清理时取消订阅
  outbound_bus.unsubscribe(session_id, my_handler)
"""

import asyncio
from dataclasses import dataclass, field
from typing import Callable, Awaitable

from loguru import logger


# ── OutboundEvent ─────────────────────────────────────────────────────────────


@dataclass
class OutboundEvent:
    """
    出站回复事件。

    当 Agent 完成（或产生增量输出）时，ChannelGateway 构造此事件
    并发布到 OutboundBus。各订阅者收到后决定如何发送给最终用户。

    Attributes:
        session_id:   所属 Session 的 ID
        text:         回复文本内容
        media_urls:   媒体文件 URL 列表（图片、文件等）
        is_final:     True = 最终完整回复；False = 增量片段（流式）
        reply_to_id:  回复哪条消息的 ID（平台消息 ID）
    """
    session_id: str
    text: str = ""
    media_urls: list[str] = field(default_factory=list)
    is_final: bool = True
    reply_to_id: str | None = None


# ── OutboundBus ───────────────────────────────────────────────────────────────


class OutboundBus:
    """
    Session 级出站事件总线。

    工作原理：
      - publish() 时，只发给订阅了该 session_id 的 handler
      - 不同 session 之间完全隔离
      - 每个 handler 独立执行，互不干扰

    与 EventBus 的设计对齐，但语义不同：
      - EventBus : Agent 内部状态变更 → 所有观察者收到
      - OutboundBus : 出站回复意图 → 只有负责发送的 adapter 收到

    Attributes:
        _subscribers: dict[session_id, list[handler]]
                      每个 session 可以有多个 handler（多平台同时回复）
    """

    def __init__(self):
        # session_id -> list[handler]
        self._subscribers: dict[
            str, list[Callable[[OutboundEvent], Awaitable[None]]]
        ] = {}

        logger.debug("OutboundBus initialized")

    # ── 订阅 ──────────────────────────────────────────────────────────────────

    def subscribe(
        self,
        session_id: str,
        handler: Callable[[OutboundEvent], Awaitable[None]],
    ) -> None:
        """
        订阅指定 session 的出站事件。

        Args:
            session_id: 要监听的 Session ID
            handler:    异步回调，收到 OutboundEvent 时调用

        Note:
            同一个 handler 多次 subscribe 同一 session 会被去重。
        """
        handlers = self._subscribers.setdefault(session_id, [])

        # 去重：同一 handler 不重复添加
        if handler in handlers:
            logger.debug(
                "OutboundBus: handler already subscribed | session={}",
                session_id[:8],
            )
            return

        handlers.append(handler)
        logger.debug(
            "OutboundBus: subscribed | session={} handlers={}",
            session_id[:8], len(handlers),
        )

    def unsubscribe(
        self,
        session_id: str,
        handler: Callable[[OutboundEvent], Awaitable[None]],
    ) -> None:
        """
        取消订阅指定 session 的出站事件。

        Args:
            session_id: Session ID
            handler:    要移除的 handler

        Note:
            handler 不存在时静默忽略。
        """
        handlers = self._subscribers.get(session_id)
        if handlers is None:
            return

        if handler in handlers:
            handlers.remove(handler)
            logger.debug(
                "OutboundBus: unsubscribed | session={} remaining={}",
                session_id[:8], len(handlers),
            )

        # 如果没有 handler 了，清理 key
        if not handlers:
            del self._subscribers[session_id]

    # ── 发布 ──────────────────────────────────────────────────────────────────

    async def publish(self, event: OutboundEvent) -> None:
        """
        发布出站事件给所有匹配的订阅者。

        只发给订阅了 event.session_id 的 handler。
        不同 handler 串行执行（避免并发导致消息顺序混乱）。

        Args:
            event: 出站事件
        """
        handlers = self._subscribers.get(event.session_id)
        if not handlers:
            logger.debug(
                "OutboundBus: no subscribers for session={}",
                event.session_id[:8],
            )
            return

        logger.debug(
            "OutboundBus: publishing | session={} is_final={} "
            "text_len={} subscribers={}",
            event.session_id[:8], event.is_final,
            len(event.text), len(handlers),
        )

        # 串行执行，保证顺序
        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.error(
                    "OutboundBus: handler failed | session={} err={}",
                    event.session_id[:8], e,
                )

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def subscriber_count(self, session_id: str) -> int:
        """返回指定 session 的订阅者数量。"""
        return len(self._subscribers.get(session_id, []))

    def has_subscribers(self, session_id: str) -> bool:
        """检查指定 session 是否有订阅者。"""
        return session_id in self._subscribers and len(self._subscribers[session_id]) > 0

    def list_sessions(self) -> list[str]:
        """返回所有有订阅者的 session_id 列表。"""
        return list(self._subscribers.keys())
