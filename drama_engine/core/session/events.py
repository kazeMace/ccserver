"""Session-scoped event storage for Drama Engine.

本模块只管理单局游戏内的事件回放和订阅队列，不认识 HTTP、SSE、
WebSocket，也不依赖 ccserver 核心 Agent 代码。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

AUDIENCE_PUBLIC = "public"
AUDIENCE_HOST = "host"
AUDIENCE_PRIVATE = "private"


@dataclass(slots=True)
class StoredEvent:
    """一条已存储的 session 事件。

    参数：
      audience: 事件受众，必须是 public / host / private。
      payload: 事件内容字典。调用方传入后会被复制，避免外部继续修改。
      seat_id: private 事件所属 seat；非 private 事件必须为 None。
    """

    audience: str
    payload: dict[str, Any]
    seat_id: str | None = None

    def __post_init__(self) -> None:
        assert self.audience in {
            AUDIENCE_PUBLIC,
            AUDIENCE_HOST,
            AUDIENCE_PRIVATE,
        }, f"未知事件 audience: {self.audience}"
        if self.audience == AUDIENCE_PRIVATE:
            assert self.seat_id, "private 事件必须带 seat_id"
        else:
            assert self.seat_id is None, "非 private 事件不能带 seat_id"
        assert isinstance(self.payload, dict), "payload 必须是 dict"


@dataclass(slots=True)
class EventSubscriber:
    """事件订阅者。

    queue 用于 Web 层实现 SSE / WebSocket。订阅者由 SessionEventStore 创建，
    Web 层只负责读取 queue，不直接写 store 内部状态。
    """

    audience: str
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    seat_id: str | None = None


class SessionEventStore:
    """单局事件存储。

    职责：
      1. 保存 public / host / private 事件回放。
      2. 管理订阅者队列。
      3. 保证事件不跨 session 泄漏。

    注意：本类是 session 级对象，不应被多个 GameRuntime 共享。
    """

    def __init__(self, session_id: str) -> None:
        assert session_id, "session_id 不能为空"
        self.session_id = session_id
        self._public_events: list[dict[str, Any]] = []
        self._host_events: list[dict[str, Any]] = []
        self._private_events: dict[str, list[dict[str, Any]]] = {}
        # 保存所有 host 可见事件的统一时序索引。
        # Store a single host-visible timeline so replay order matches live SSE order.
        self._host_timeline: list[dict[str, Any]] = []
        self._subscribers: list[EventSubscriber] = []
        self._next_seq = 1
        logger.info("[SessionEventStore] 初始化：session=%s", session_id)

    def append_public(self, event: dict[str, Any]) -> None:
        """追加公开事件并推送给 public / host 订阅者。"""
        payload = self._prepare_event(event, AUDIENCE_PUBLIC, None)
        self._public_events.append(payload)
        self._host_timeline.append(dict(payload))
        self._publish(AUDIENCE_PUBLIC, None, payload)
        logger.debug("[SessionEventStore] public event: session=%s", self.session_id)

    def append_host(self, event: dict[str, Any]) -> None:
        """追加主持人事件，只推送给 host 订阅者。"""
        payload = self._prepare_event(event, AUDIENCE_HOST, None)
        self._host_events.append(payload)
        self._host_timeline.append(dict(payload))
        self._publish(AUDIENCE_HOST, None, payload)
        logger.debug("[SessionEventStore] host event: session=%s", self.session_id)

    def append_private(self, seat_id: str, event: dict[str, Any]) -> None:
        """追加指定 seat 的私密事件。"""
        assert seat_id, "seat_id 不能为空"
        payload = self._prepare_event(event, AUDIENCE_PRIVATE, seat_id)
        self._private_events.setdefault(seat_id, []).append(payload)
        self._publish(AUDIENCE_PRIVATE, seat_id, payload)
        logger.debug(
            "[SessionEventStore] private event: session=%s seat=%s",
            self.session_id,
            seat_id,
        )

    def public_backlog(self) -> list[dict[str, Any]]:
        """返回公开事件回放副本。"""
        return [dict(event) for event in self._public_events]

    def host_backlog(self) -> list[dict[str, Any]]:
        """返回主持人视角回放副本。

        主持人应该能看到公开事件和 host-only 事件，并且顺序必须和实时 SSE
        完全一致；否则 dashboard 回放会把先后的对话、状态、通报打乱。
        """
        return [dict(event) for event in self._host_timeline]

    def private_backlog(self, seat_id: str) -> list[dict[str, Any]]:
        """返回指定 seat 的私密事件回放副本。"""
        assert seat_id, "seat_id 不能为空"
        return [dict(event) for event in self._private_events.get(seat_id, [])]

    def subscribe_public(self) -> EventSubscriber:
        """订阅公开事件流。"""
        subscriber = EventSubscriber(audience=AUDIENCE_PUBLIC)
        self._subscribers.append(subscriber)
        for event in self.public_backlog():
            subscriber.queue.put_nowait(event)
        return subscriber

    def subscribe_host(self) -> EventSubscriber:
        """订阅主持人事件流。"""
        subscriber = EventSubscriber(audience=AUDIENCE_HOST)
        self._subscribers.append(subscriber)
        for event in self.host_backlog():
            subscriber.queue.put_nowait(event)
        return subscriber

    def subscribe_private(self, seat_id: str) -> EventSubscriber:
        """订阅指定 seat 的私密事件流。"""
        assert seat_id, "seat_id 不能为空"
        subscriber = EventSubscriber(audience=AUDIENCE_PRIVATE, seat_id=seat_id)
        self._subscribers.append(subscriber)
        for event in self.private_backlog(seat_id):
            subscriber.queue.put_nowait(event)
        return subscriber

    def unsubscribe(self, subscriber: EventSubscriber) -> None:
        """取消订阅。"""
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)

    def clear_backlog(self) -> None:
        """清空事件回放但保留当前 SSE 订阅者。

        restart 需要清掉上一局历史，不能断开已经打开的 Host dashboard。
        """
        self._public_events = []
        self._host_events = []
        self._private_events = {}
        self._host_timeline = []
        self._next_seq = 1

    def _publish(self, audience: str, seat_id: str | None, payload: dict[str, Any]) -> None:
        """把事件推送给匹配的订阅者。"""
        for subscriber in list(self._subscribers):
            should_send = False
            if audience == AUDIENCE_PUBLIC:
                should_send = subscriber.audience in {AUDIENCE_PUBLIC, AUDIENCE_HOST}
            elif audience == AUDIENCE_HOST:
                should_send = subscriber.audience == AUDIENCE_HOST
            elif audience == AUDIENCE_PRIVATE:
                should_send = (
                    subscriber.audience == AUDIENCE_PRIVATE
                    and subscriber.seat_id == seat_id
                )
            if should_send:
                subscriber.queue.put_nowait(dict(payload))

    def dump(self) -> dict[str, Any]:
        """导出事件回放，供持久化存储使用。"""
        return {
            "session_id": self.session_id,
            "next_seq": self._next_seq,
            "public_events": self.public_backlog(),
            "host_events": [dict(event) for event in self._host_events],
            "private_events": {
                seat_id: self.private_backlog(seat_id)
                for seat_id in self._private_events.keys()
            },
        }

    def load(self, data: dict[str, Any]) -> None:
        """从持久化字典恢复事件回放。"""
        assert isinstance(data, dict), "event data 必须是 dict"
        self._public_events = [dict(event) for event in data.get("public_events") or []]
        self._host_events = [dict(event) for event in data.get("host_events") or []]
        self._host_timeline = sorted(
            [dict(event) for event in self._public_events + self._host_events],
            key=lambda event: int(event.get("seq") or 0),
        )
        raw_private = data.get("private_events") or {}
        assert isinstance(raw_private, dict), "private_events 必须是 dict"
        self._private_events = {
            str(seat_id): [dict(event) for event in events]
            for seat_id, events in raw_private.items()
        }
        max_seq = 0
        for event in self._public_events + self._host_events:
            max_seq = max(max_seq, int(event.get("seq") or 0))
        for events in self._private_events.values():
            for event in events:
                max_seq = max(max_seq, int(event.get("seq") or 0))
        self._next_seq = max(int(data.get("next_seq") or 1), max_seq + 1)

    def _prepare_event(
        self,
        event: dict[str, Any],
        audience: str,
        seat_id: str | None,
    ) -> dict[str, Any]:
        """复制、校验并补齐稳定 ViewEvent 元信息。

        P1 起所有事件都带 session_id / seq / audience。新前端主读 type，
        但这里继续补齐 kind，兼容旧回放和旧客户端。
        """
        assert isinstance(event, dict), "event 必须是 dict"
        payload = dict(event)
        event_type = payload.get("type") or payload.get("kind")
        assert event_type, "event.type 不能为空"
        payload.setdefault("type", event_type)
        payload.setdefault("kind", event_type)
        payload.setdefault("session_id", self.session_id)
        # 服务端事件流的 seq 必须由 SessionEventStore 统一分配。
        # The tracer also has an internal seq; preserve it as trace_seq instead
        # of letting it collide with session lifecycle events. Frontend de-dupes
        # by seq, so collisions used to hide many dialogue bubbles.
        source_seq = payload.get("seq")
        if source_seq is not None:
            payload.setdefault("trace_seq", source_seq)
        payload["seq"] = self._next_seq
        payload["audience"] = audience
        if seat_id is not None:
            payload.setdefault("seat_id", seat_id)
        self._next_seq += 1
        logger.debug(
            "[SessionEventStore] prepared event: session=%s audience=%s seq=%s type=%s",
            self.session_id,
            audience,
            payload["seq"],
            event_type,
        )
        return payload
