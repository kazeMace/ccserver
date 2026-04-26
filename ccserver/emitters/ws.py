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

    支持 EventBus 直接订阅（P1）：
    - 通过 start_event_bus_subscription() 订阅 EventBus，
      收到 AgentEvent 后自动转换为 WS 格式并直接发送。
    - 客户端断开时调用 stop_event_bus_subscription() 注销订阅，防止泄漏。
    """

    def __init__(self, websocket: WebSocket, session=None, event_bus=None, client_id=None):
        self._ws = websocket
        self._session = session

        # ── EventBus 订阅相关（P1）───────────────────────────────────────────
        self._event_bus = event_bus          # EventBus 实例，可为 None
        self._client_id = client_id          # 客户端标识，用于构造 subscriber_id
        self._event_bus_task: asyncio.Task | None = None   # 订阅协程任务
        self._event_bus_sub_id: str | None = None          # 订阅者 ID

    async def emit(self, event: dict) -> None:
        # hook: message:outbound:sending — 发送前拦截（observing）
        event = await self._emit_with_hook(event, self._session)
        await self._ws.send_json(event)

    # ── EventBus 订阅（P1：WSEmitter 直接订阅 EventBus）──────────────────────

    async def start_event_bus_subscription(self, filter_fn) -> None:
        """
        启动 EventBus 订阅，收到的事件自动转换为 WS 格式并通过 WebSocket 发送。

        Args:
            filter_fn: 事件过滤函数，接收 AgentEvent 返回 bool。
                       常用示例：
                           lambda e: e.session_id == session_id

        Note:
            必须在 EventBus 实例存在时调用（event_bus 参数不为 None）。
            多次调用会自动取消前一次的订阅。
        """
        if self._event_bus is None:
            return

        # 若已有订阅，先停止旧的
        await self.stop_event_bus_subscription()

        sub_id = f"ws_{self._client_id or id(self)}"
        self._event_bus_sub_id = sub_id

        async def _event_bus_loop():
            """
            EventBus 订阅协程。

            使用 async with 订阅，退出时自动 unsubscribe。
            持续从 Subscription 获取事件，转换为 WS 格式后直接发送。
            """
            async with self._event_bus.subscribe(sub_id, filter_fn=filter_fn) as sub:
                while True:
                    try:
                        event = await sub.get(timeout=1.0)
                    except asyncio.CancelledError:
                        break
                    if event is None:
                        continue

                    # 将 AgentEvent 转换为 WS 格式的 dict
                    ws_event = self._convert_agent_event(event)
                    if ws_event is not None:
                        try:
                            await self._ws.send_json(ws_event)
                        except Exception:
                            # WebSocket 可能已关闭，忽略发送错误
                            break

        self._event_bus_task = asyncio.create_task(_event_bus_loop())

    async def stop_event_bus_subscription(self) -> None:
        """
        停止 EventBus 订阅。

        取消订阅协程任务，并等待其清理完成（Subscription 的 __aexit__ 会自动 unsubscribe）。
        """
        if self._event_bus_task is not None and not self._event_bus_task.done():
            self._event_bus_task.cancel()
            try:
                await self._event_bus_task
            except asyncio.CancelledError:
                pass
        self._event_bus_task = None
        self._event_bus_sub_id = None

    def _convert_agent_event(self, event) -> dict | None:
        """
        将 AgentEvent 转换为 WS emitter 的 dict 格式。

        映射规则（与 BusEmitter 的 emit_* 方法对应）：
          - TOKEN        → fmt_token
          - TOOL_START   → fmt_tool_start
          - TOOL_DONE    → fmt_tool_result
          - PROGRESS     → fmt_task_progress（含 task_id 时）或忽略
          - DONE         → fmt_done
          - ERROR        → fmt_error
          - CANCELLED    → fmt_error
          - TASK_STARTED → fmt_task_started
          - TASK_DONE    → fmt_task_done

        Args:
            event: AgentEvent 实例。

        Returns:
            WS 格式的 dict，或 None（该事件无需推送到客户端）。
        """
        from ccserver.event_bus import EventType

        payload = event.payload
        etype = event.type

        if etype == EventType.TOKEN:
            return self.fmt_token(payload.get("token", ""))

        if etype == EventType.TOOL_START:
            return self.fmt_tool_start(
                payload.get("tool_name", ""),
                payload.get("preview", ""),
            )

        if etype == EventType.TOOL_DONE:
            # BusEmitter 存储的是 "result"，fmt_tool_result 期望 "output"
            return self.fmt_tool_result(
                payload.get("tool_name", ""),
                payload.get("result", ""),
            )

        if etype == EventType.PROGRESS:
            task_id = payload.get("task_id")
            if task_id:
                return self.fmt_task_progress(
                    task_id=task_id,
                    status=payload.get("status", "running"),
                    output=payload.get("output", ""),
                    progress=payload.get("progress"),
                )
            return None

        if etype == EventType.DONE:
            return self.fmt_done(payload.get("content", ""))

        if etype == EventType.ERROR:
            # BusEmitter 存储的是 "error"，fmt_error 期望 "message"
            return self.fmt_error(payload.get("error", "unknown error"))

        if etype == EventType.CANCELLED:
            return self.fmt_error(payload.get("reason", "cancelled"))

        if etype == "task_started":
            return self.fmt_task_started(
                task_id=payload.get("task_id", ""),
                task_type=payload.get("task_type", ""),
                description=payload.get("description", ""),
                pid=payload.get("pid"),
            )

        if etype == "task_done":
            return self.fmt_task_done(
                task_id=payload.get("task_id", ""),
                status=payload.get("status", ""),
                output=payload.get("output", ""),
                exit_code=payload.get("exit_code"),
                reason=payload.get("reason"),
            )

        return None

    # ── AskUserQuestion / Permission 双向交互 ────────────────────────────────

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
