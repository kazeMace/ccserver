import asyncio
import json
from typing import AsyncIterator, Optional

from fastapi import WebSocket

from . import BaseEmitter


class SSEEmitter(BaseEmitter):
    """
    将事件缓冲到 asyncio.Queue 中。
    SSE 路由从队列中取出事件，格式化为 `data: ...\n\n` 推送给客户端。

    支持 AskUserQuestion 双向交互：
    - emit_ask_user() 推送 ask_user 事件后，挂起等待 inject_answer() 被调用。
    - 客户端收到 ask_user 事件后，调用 POST /chat/stream/answer 传入答案。
    - inject_answer() 将答案写入，并唤醒挂起的 emit_ask_user()。
    """

    def __init__(self):
        self._queue: asyncio.Queue[Optional[dict]] = asyncio.Queue()
        # 用于 AskUserQuestion 的等待机制
        self._answer_event: asyncio.Event = asyncio.Event()
        self._answer: str = ""
        # 用于 permission_request 的等待机制（复用同一套 Event，串行执行）
        self._permission_event: asyncio.Event = asyncio.Event()
        self._permission_granted: bool = False

    async def emit(self, event: dict) -> None:
        await self._queue.put(event)

    async def event_stream(self) -> AsyncIterator[str]:
        while True:
            event = await self._queue.get()
            if event is None:  # 哨兵值 — 代理已结束
                break
            yield json.dumps(event)

    async def close(self):
        await self._queue.put(None)

    async def emit_ask_user(self, questions: list) -> str:
        """
        推送 ask_user 事件，然后阻塞等待客户端通过 inject_answer() 提供答案。
        返回用户的答案字符串。
        """
        # 重置状态，准备接收新答案
        self._answer = ""
        self._answer_event.clear()

        # 推送事件到 SSE 流，客户端收到后应提示用户回答
        await self.emit(self.fmt_ask_user(questions))

        # 阻塞等待，直到 inject_answer() 被调用（超时 5 分钟）
        try:
            await asyncio.wait_for(self._answer_event.wait(), timeout=300)
        except asyncio.TimeoutError:
            return "(no answer: timed out after 5 minutes)"

        return self._answer

    def inject_answer(self, answer: str) -> None:
        """
        由 API 层调用，将客户端的回答注入进来，唤醒挂起的 emit_ask_user()。
        """
        self._answer = answer
        self._answer_event.set()

    async def emit_permission_request(self, tool_name: str, tool_input: dict) -> bool:
        """
        推送 permission_request 事件，阻塞等待客户端通过 inject_permission() 给出决定。
        返回 True 表示用户批准，False 表示用户拒绝或超时（5 分钟）。
        """
        self._permission_granted = False
        self._permission_event.clear()

        await self.emit(self.fmt_permission_request(tool_name, tool_input))

        try:
            await asyncio.wait_for(self._permission_event.wait(), timeout=300)
        except asyncio.TimeoutError:
            return False

        return self._permission_granted

    def inject_permission(self, granted: bool) -> None:
        """
        由 API 层调用，将客户端的批准/拒绝决定注入，唤醒挂起的 emit_permission_request()。
        """
        self._permission_granted = granted
        self._permission_event.set()


class WSEmitter(BaseEmitter):
    """
    将事件直接写入 WebSocket 连接。
    与 SSEEmitter 使用相同的 fmt_* 方法。

    支持 AskUserQuestion 双向交互：
    - emit_ask_user() 推送 ask_user 事件后，直接从 WebSocket 读取下一条消息作为答案。
    - 客户端收到 ask_user 事件后，发送 {"answer": "..."} 消息即可。
    """

    def __init__(self, websocket: WebSocket):
        self._ws = websocket

    async def emit(self, event: dict) -> None:
        await self._ws.send_json(event)

    async def emit_ask_user(self, questions: list) -> str:
        """
        推送 ask_user 事件，然后等待客户端发送下一条包含 answer 字段的消息。
        返回用户的答案字符串。
        """
        await self.emit(self.fmt_ask_user(questions))

        # 等待客户端回复，期望格式：{"answer": "用户的回答"}
        try:
            raw = await asyncio.wait_for(self._ws.receive_text(), timeout=300)
            payload = json.loads(raw)
            return payload.get("answer", raw)
        except asyncio.TimeoutError:
            return "(no answer: timed out after 5 minutes)"
        except Exception:
            return ""

    async def emit_permission_request(self, tool_name: str, tool_input: dict) -> bool:
        """
        推送 permission_request 事件，等待客户端发送 {"granted": true/false} 消息。
        返回 True 表示用户批准，False 表示拒绝或超时。
        """
        await self.emit(self.fmt_permission_request(tool_name, tool_input))

        # 等待客户端回复，期望格式：{"granted": true}
        try:
            raw = await asyncio.wait_for(self._ws.receive_text(), timeout=300)
            payload = json.loads(raw)
            return bool(payload.get("granted", False))
        except asyncio.TimeoutError:
            return False
        except Exception:
            return False


class CollectEmitter(BaseEmitter):
    """
    将所有事件收集到内存中。
    用于普通 HTTP（非流式）响应。
    """

    def __init__(self):
        self.events: list[dict] = []

    async def emit(self, event: dict) -> None:
        self.events.append(event)

    def get_final_text(self) -> str:
        for e in reversed(self.events):
            if e["type"] == "done":
                return e.get("content", "")
        return ""
