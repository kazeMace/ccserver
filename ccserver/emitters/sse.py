import asyncio
import json
from typing import AsyncIterator, Optional

from .base import BaseEmitter


class SSEEmitter(BaseEmitter):
    """
    将事件缓冲到 asyncio.Queue 中。
    SSE 路由从队列中取出事件，格式化为 `data: ...\n\n` 推送给客户端。

    支持 AskUserQuestion 双向交互：
    - emit_ask_user() 推送 ask_user 事件后，挂起等待 inject_answer() 被调用。
    - 客户端收到 ask_user 事件后，调用 POST /chat/stream/answer 传入答案。
    - inject_answer() 将答案写入，并唤醒挂起的 emit_ask_user()。
    """

    def __init__(self, session=None):
        self._queue: asyncio.Queue[Optional[dict]] = asyncio.Queue()
        # 用于 AskUserQuestion 的等待机制
        self._answer_event: asyncio.Event = asyncio.Event()
        self._answer: str = ""
        # 用于 permission_request 的等待机制（复用同一套 Event，串行执行）
        self._permission_event: asyncio.Event = asyncio.Event()
        self._permission_granted: bool = False
        # session 引用，用于触发 message:outbound:sending hook
        self._session = session

    async def emit(self, event: dict) -> None:
        # hook: message:outbound:sending — 发送前拦截（observing）
        # 复用父类的 _emit_with_hook 触发 hook（异步 fire-and-forget）
        event = await self._emit_with_hook(event, self._session)
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
