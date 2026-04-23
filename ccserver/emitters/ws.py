import asyncio
import json

from fastapi import WebSocket

from .base import BaseEmitter


class WSEmitter(BaseEmitter):
    """
    将事件直接写入 WebSocket 连接。
    与 SSEEmitter 使用相同的 fmt_* 方法。

    支持 AskUserQuestion 双向交互：
    - emit_ask_user() 推送 ask_user 事件后，直接从 WebSocket 读取下一条消息作为答案。
    - 客户端收到 ask_user 事件后，发送 {"answer": "..."} 消息即可。
    """

    def __init__(self, websocket: WebSocket, session=None):
        self._ws = websocket
        self._session = session

    async def emit(self, event: dict) -> None:
        # hook: message:outbound:sending — 发送前拦截（observing）
        event = await self._emit_with_hook(event, self._session)
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
