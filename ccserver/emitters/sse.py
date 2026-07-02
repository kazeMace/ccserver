import asyncio
import json
from typing import AsyncIterator, Optional, TYPE_CHECKING

from .base import BaseEmitter

if TYPE_CHECKING:
    from ccserver.builtins.tools.base import ToolResult


class SSEEmitter(BaseEmitter):
    """
    将事件缓冲到 asyncio.Queue 中。
    SSE 路由从队列中取出事件，格式化为 `data: ...\n\n` 推送给客户端。

    支持 AskUserQuestion 双向交互：
    - emit_ask_user() 推送 ask_user 事件后，挂起等待 inject_answer() 被调用。
    - 客户端收到 ask_user 事件后，调用 POST /chat/stream/answer 传入答案。
    - inject_answer() 将答案写入，并唤醒挂起的 emit_ask_user()。

    支持 EventBus 直接订阅（P1）：
    - 通过 start_event_bus_subscription() 订阅 EventBus，
      收到 AgentEvent 后自动转换为 SSE 格式并放入队列。
    - 客户端断开时调用 stop_event_bus_subscription() 注销订阅，防止泄漏。
    """

    def __init__(self, session=None, event_bus=None, client_id=None, root_agent_id=None):
        self._queue: asyncio.Queue[Optional[dict]] = asyncio.Queue()
        # 用于 AskUserQuestion 的等待机制（老 SSE 直连流）
        self._answer_event: asyncio.Event = asyncio.Event()
        self._answer: str = ""
        # 用于 permission_request 的等待机制（复用同一套 Event，串行执行）
        self._permission_event: asyncio.Event = asyncio.Event()
        self._permission_granted: bool = False
        # session 引用，用于触发 message:outbound:sending hook
        self._session = session

        # ── 根 Agent ID：用于 EventBus 事件过滤 ──────────────────────────────
        # token/tool_start/tool_done/done/error 只推送根 Agent 的事件；
        # task_started/task_done/progress（含 task_id）对所有 agent 都推送。
        # 初始为 None，由 set_root_agent_id() 在 Agent 创建后注入。
        self._root_agent_id: Optional[str] = root_agent_id

        # ── 新出站架构：Processor callback 机制 ──────────────────────────────
        # Gateway 流中由 WebChatProcessor.on_ask_user() 设置，
        # inject_answer() 时优先调用 callback 而非 asyncio.Event 机制
        self._answer_cb = None       # Optional[Callable[[str], None]]
        self._grant_cb = None        # Optional[Callable[[bool], None]]

        # ── EventBus 订阅相关（P1）───────────────────────────────────────────
        self._event_bus = event_bus          # EventBus 实例，可为 None
        self._client_id = client_id          # 客户端标识，用于构造 subscriber_id
        self._event_bus_task: asyncio.Task | None = None   # 订阅协程任务
        self._event_bus_sub_id: str | None = None          # 订阅者 ID

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
        """
        关闭 SSEEmitter。

        1. 停止 EventBus 订阅（如果已启动）
        2. 向队列放入哨兵值，通知 event_stream() 结束
        """
        await self.stop_event_bus_subscription()
        await self._queue.put(None)

    # ── EventBus 订阅（P1：SSEEmitter 直接订阅 EventBus）──────────────────────

    async def start_event_bus_subscription(
        self, filter_fn, last_event_id: Optional[str] = None
    ) -> None:
        """
        启动 EventBus 订阅，收到的事件自动转换为 SSE 格式并推入队列。

        Args:
            filter_fn:     事件过滤函数，接收 AgentEvent 返回 bool。
                           常用示例：
                               lambda e: e.session_id == session_id
            last_event_id: P2-4 断线重连时，携带上次收到的 event_id。
                           不为 None 时先从 EventBus 重放缓冲区回放历史事件，
                           再启动实时订阅，避免用户看到内容跳空。

        Note:
            必须在 EventBus 实例存在时调用（event_bus 参数不为 None）。
            多次调用会自动取消前一次的订阅。
        """
        if self._event_bus is None:
            return

        # 若已有订阅，先停止旧的
        await self.stop_event_bus_subscription()

        sub_id = f"sse_{self._client_id or id(self)}"
        self._event_bus_sub_id = sub_id

        # P2-4：断线重连时先回放历史事件
        if last_event_id is not None and hasattr(self._event_bus, "replay_since"):
            replayed = self._event_bus.replay_since(
                last_event_id=last_event_id,
                filter_fn=filter_fn,
            )
            for past_event in replayed:
                sse_event = self._convert_agent_event(past_event)
                if sse_event is not None:
                    await self._queue.put(sse_event)
            if replayed:
                import json as _json
                # 回放结束标记，客户端可选择性展示
                await self._queue.put({"type": "replay_end", "count": len(replayed)})

        async def _event_bus_loop():
            """
            EventBus 订阅协程。

            使用 async with 订阅，退出时自动 unsubscribe。
            持续从 Subscription 获取事件，转换为 SSE 格式后推入 _queue。
            """
            async with self._event_bus.subscribe(sub_id, filter_fn=filter_fn) as sub:
                while True:
                    try:
                        event = await sub.get(timeout=1.0)
                    except asyncio.CancelledError:
                        break
                    if event is None:
                        continue

                    # 将 AgentEvent 转换为 SSE 格式的 dict
                    sse_event = self._convert_agent_event(event)
                    if sse_event is not None:
                        await self._queue.put(sse_event)

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

    def set_root_agent_id(self, agent_id: str) -> None:
        """
        设置根 Agent ID。

        由 runner.run() 创建 Agent 后立即调用，让 SSEEmitter 知道哪个 Agent 的
        token/tool_start/done/error 事件需要推送，哪些是子 Agent 的应该过滤掉。

        Args:
            agent_id: 根 Agent 的 agent_id（AgentContext.agent_id）
        """
        self._root_agent_id = agent_id

    def _convert_agent_event(self, event) -> Optional[dict]:
        """
        将 AgentEvent 转换为 SSE emitter 的 dict 格式。

        agent_id 过滤规则：
          - token / tool_start / tool_done / done / error / cancelled / ask_user：
            只推送根 Agent（self._root_agent_id）的事件，子 Agent 的忽略。
            root_agent_id 未设置时（None），放行所有（兼容旧路径）。
          - task_started / task_done / progress（含 task_id）/ image：
            所有 Agent 的都推送（后台任务状态需要呈现给用户）。

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
            SSE 格式的 dict，或 None（该事件无需推送到客户端）。
        """
        from ccserver.event_bus import EventType

        payload = event.payload
        etype = event.type

        # ── agent_id 过滤：流式内容事件只推送根 Agent 的 ──────────────────────
        # root_agent_id 设置后，非根 Agent 的 token/done/error/tool 事件一律丢弃，
        # 防止子 Agent 的中间过程与根 Agent 的流混杂，导致客户端渲染错乱。
        _stream_types = {
            EventType.TOKEN, EventType.TOOL_START, EventType.TOOL_DONE,
            EventType.DONE, EventType.ERROR, EventType.CANCELLED,
            EventType.ASK_USER,
        }
        if etype in _stream_types and self._root_agent_id is not None:
            if event.agent_id != self._root_agent_id:
                return None  # 子 Agent 的流式事件，丢弃

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
            # 没有 task_id 的 progress 事件（如根 Agent 的进度），暂不推送给客户端
            return None

        if etype == EventType.DONE:
            return self.fmt_done(payload.get("content", ""))

        if etype == EventType.ERROR:
            # BusEmitter 存储的是 "error"，fmt_error 期望 "message"
            return self.fmt_error(payload.get("error", "unknown error"))

        if etype == EventType.CANCELLED:
            return self.fmt_error(payload.get("reason", "cancelled"))

        # task_started / task_done 也可能通过 EventBus 发布（虽然当前主要由父 emitter 推送）
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

        # 图像内容事件 — 推送图像数据给前端渲染
        if etype == EventType.IMAGE:
            return self._fmt(
                "screen_image",
                tool=payload.get("tool_name", "ScreenCapture"),
                image_base64=payload.get("image_base64", ""),
                description=payload.get("description", ""),
            )

        # 其他事件类型（如 idle、new_task、permission_req 等）不推送给 SSE 客户端
        return None

    async def emit_tool_result_with_image(self, name: str, result: "ToolResult") -> None:
        """
        SSE 模式：发送含图像的工具结果。

        推送两个事件到队列：
        1. tool_result — 文本描述（与普通工具结果格式一致）
        2. screen_image — 图像 base64（前端渲染预览图）

        Args:
            name:   工具名称。
            result: 多模态 ToolResult（has_image=True）。
        """
        # 1. 文字描述
        await self.emit(self.fmt_tool_result(name, result.content_text))

        # 2. 图像事件（缩略图优先）
        img_b64 = result.get_thumbnail_base64() or result.get_image_base64()
        if img_b64:
            await self.emit(self._fmt(
                "screen_image",
                tool=name,
                image_base64=img_b64,
                description=result.content_text,
            ))

    # ── AskUserQuestion / Permission 双向交互 ────────────────────────────────

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
        由 API 层调用，将客户端的回答注入进来。

        支持两种模式：
          1. 新出站架构（Gateway 流）：若 _answer_cb 已设置，调用 callback 注入答案。
          2. 旧直连流（server.py 直接调用 agent.run）：通过 asyncio.Event 唤醒 emit_ask_user()。
        """
        # 优先：新 callback 机制（WebChatProcessor 设置）
        if self._answer_cb is not None:
            cb = self._answer_cb
            self._answer_cb = None
            cb(answer)
        # 兼容：旧 asyncio.Event 机制
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
        由 API 层调用，将客户端的批准/拒绝决定注入。

        支持两种模式（与 inject_answer 相同）：
          1. 新出站架构：若 _grant_cb 已设置，调用 callback 注入决定。
          2. 旧直连流：通过 asyncio.Event 唤醒 emit_permission_request()。
        """
        if self._grant_cb is not None:
            cb = self._grant_cb
            self._grant_cb = None
            cb(granted)
        self._permission_granted = granted
        self._permission_event.set()
