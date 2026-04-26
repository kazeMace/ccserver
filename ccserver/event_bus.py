"""
event_bus — Session 级 Agent 事件总线。

设计目标
────────
- 任意数量的 Agent 向总线 publish 事件（发布者）
- 任意数量的观察者独立订阅总线（订阅者），互不影响
- 支持按 to_agent / event type 过滤，实现点对点或广播
- Queue 消费互不干扰：每个订阅者有独立的副本队列

类比计算机总线：Agent 是插在总线上的设备，插拔自由，不感知彼此。

典型用法
────────
发布方（Agent 通过 BusEmitter 自动调用，无需手动调用）：
    await session.event_bus.publish(AgentEvent(...))

订阅方：
    async with session.event_bus.subscribe("my_handler") as sub:
        async for event in sub:
            if event.type == "done":
                break
            print(event.payload)

或带过滤器（只收发给自己的消息）：
    filter_fn = lambda e: e.to_agent == my_agent_id
    async with session.event_bus.subscribe("my_handler", filter_fn=filter_fn) as sub:
        async for event in sub:
            ...
"""

import asyncio
import time
import uuid

from dataclasses import dataclass, field
from typing import Callable, Optional

from loguru import logger


# ── 事件类型常量 ──────────────────────────────────────────────────────────────

class EventType:
    """
    AgentEvent 的 type 字段取值。

    每个常量对应一种事件：
      - token            LLM 流式输出的一个 token 片段
      - tool_start       工具开始执行（含工具名和输入参数）
      - tool_done        工具执行完毕（含输出结果）
      - progress         Agent 轮次进度（当前轮/总轮/phase/当前工具）
      - done             Agent 任务完成（含最终输出）
      - error            Agent 出错（含错误信息）
      - cancelled        Agent 被取消
      - idle             Teammate 进入空闲，等待新任务
      - new_task         向 Teammate 投递新任务
      - permission_req   Teammate 向 Lead 请求工具权限审批
      - permission_resp  Lead 回复权限审批结果
      - shutdown         请求 Teammate 优雅退出
      - phase_changed    Agent 状态变化（idle/running/llm_calling/tool_executing/done/error）
      - llm_request      LLM 请求发送（含模型、消息数、工具数）
      - llm_response     LLM 响应接收（含模型、stop_reason、耗时）
      - llm_error        LLM 调用失败（含错误类型、重试次数）
      - llm_retry        LLM 网络错误重试
      - subagent_spawned 子 Agent 创建（含父/子 ID、深度、模式）
      - message_appended 消息追加到对话历史
      - message_compacted 消息压缩完成
      - hook_executed    Hook 执行完成
    """
    TOKEN           = "token"
    TOOL_START      = "tool_start"
    TOOL_DONE       = "tool_done"
    PROGRESS        = "progress"
    DONE            = "done"
    ERROR           = "error"
    CANCELLED       = "cancelled"
    IDLE            = "idle"
    NEW_TASK        = "new_task"
    PERMISSION_REQ  = "permission_req"
    PERMISSION_RESP = "permission_resp"
    SHUTDOWN        = "shutdown"
    PHASE_CHANGED   = "phase_changed"
    LLM_REQUEST     = "llm_request"
    LLM_RESPONSE    = "llm_response"
    LLM_ERROR       = "llm_error"
    LLM_RETRY       = "llm_retry"
    SUBAGENT_SPAWNED = "subagent_spawned"
    MESSAGE_APPENDED = "message_appended"
    MESSAGE_COMPACTED = "message_compacted"
    HOOK_EXECUTED   = "hook_executed"


# ── AgentEvent ────────────────────────────────────────────────────────────────

# ── 发送者类型常量 ──────────────────────────────────────────────────────────────

class SenderType:
    """AgentEvent 的 sender_type 字段取值，用于区分事件来源类型。"""
    AGENT   = "agent"    # 真正的 Agent（根 Agent / 子 Agent）
    GATEWAY = "gateway"  # Channel Gateway（飞书/Discord/钉钉等）
    SYSTEM  = "system"   # 系统组件（dispatcher、monitor 等）


@dataclass
class AgentEvent:
    """
    总线上流通的统一事件格式。

    每条事件都带有发送方身份（agent_id）、所属 Session（session_id）、
    可选的目标（to_agent）以及事件内容（payload）。

    Args:
        type:        事件类型，取值见 EventType。
        agent_id:    发送方的唯一 ID。
        session_id:  所属 Session 的 ID。
        payload:     事件内容，不同类型有不同字段，详见各 EventType 说明。
        sender_type: 发送方类型，取值见 SenderType。用于区分是真正的 Agent、
                     Gateway 还是系统组件产生的事件。
        to_agent:    目标 Agent ID。None 表示广播给所有订阅者；
                     指定值时只有 filter_fn 匹配的订阅者才会收到。
        event_id:    事件唯一 ID，自动生成，用于去重和追踪。
        ts:          事件产生时的单调时间戳（time.monotonic()）。

    payload 常用字段（按 type）：
        token:          {"token": str}
        tool_start:     {"tool_name": str, "tool_input": dict}
        tool_done:      {"tool_name": str, "result": str}
        progress:       {"round_num": int, "max_rounds": int,
                          "phase": str, "current_tool": str | None}
        done:           {"content": str}
        error:          {"error": str}
        idle:           {"completed_task_id": str | None}
        new_task:       {"task_id": str, "task_prompt": str}
        permission_req: {"request_id": str, "tool_name": str,
                          "tool_input": dict, "description": str}
        permission_resp:{"request_id": str, "approved": bool,
                          "feedback": str | None}
    """
    type:        str
    agent_id:    str
    session_id:  str
    payload:     dict = field(default_factory=dict)
    sender_type: str = field(default=SenderType.AGENT)
    to_agent:    Optional[str] = None
    event_id:    str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    ts:          float = field(default_factory=time.monotonic)


# ── Subscription ──────────────────────────────────────────────────────────────

class Subscription:
    """
    订阅句柄，通过 async with 使用，退出时自动注销。

    使用方式：
        async with bus.subscribe("my_id") as sub:
            async for event in sub:
                handle(event)
        # 退出 with 块后自动调用 bus.unsubscribe("my_id")

    不要直接实例化，通过 EventBus.subscribe() 获取。
    """

    def __init__(self, bus: "EventBus", subscriber_id: str, queue: asyncio.Queue):
        # 持有总线引用，用于退出时注销
        self._bus = bus
        self._subscriber_id = subscriber_id
        self._queue = queue

    async def __aenter__(self) -> "Subscription":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # 不管是否异常都注销，防止订阅者泄漏
        self._bus.unsubscribe(self._subscriber_id)

    def __aiter__(self):
        return self

    async def __anext__(self) -> AgentEvent:
        """
        从订阅队列取下一条事件，永久阻塞直到有事件到来。
        如果需要超时，请在外部用 asyncio.wait_for 包裹。
        """
        event = await self._queue.get()
        return event

    async def get(self, timeout: float | None = None) -> Optional[AgentEvent]:
        """
        取一条事件。

        Args:
            timeout: 超时秒数，None 表示永久等待。

        Returns:
            事件对象，超时则返回 None。
        """
        if timeout is None:
            return await self._queue.get()
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None


# ── EventBus ──────────────────────────────────────────────────────────────────

class EventBus:
    """
    Session 级广播事件总线。

    工作原理：
        每个订阅者注册后得到一个独立的 asyncio.Queue。
        publish() 时遍历所有订阅者，把事件副本逐一放入各自的队列。
        因此不同订阅者之间完全独立，互不竞争。

    背压处理：
        每个订阅者 Queue 有 maxsize 上限。
        如果某个订阅者的 Queue 已满（消费太慢），会丢弃最旧的一条
        并 log warning，不阻塞 publish 方。

    线程安全：
        本类仅用于 asyncio 协程，不支持多线程并发访问。
    """

    # 订阅者 Queue 的默认最大容量
    DEFAULT_QUEUE_MAXSIZE = 256

    def __init__(self):
        # subscriber_id -> (asyncio.Queue, filter_fn)
        # filter_fn: 接收 AgentEvent 返回 bool，None 表示接收所有事件
        self._subscribers: dict[str, tuple[asyncio.Queue, Optional[Callable]]] = {}

    def subscribe(
        self,
        subscriber_id: str,
        filter_fn: Optional[Callable[[AgentEvent], bool]] = None,
        maxsize: int = DEFAULT_QUEUE_MAXSIZE,
    ) -> Subscription:
        """
        注册一个订阅者，返回 Subscription 上下文管理器。

        Args:
            subscriber_id: 订阅者唯一标识，用于注销时定位。
                           建议命名格式："{用途}_{agent_id 前8位}"，例如 "sse_a1b2c3d4"。
            filter_fn:     可选过滤函数。接收 AgentEvent，返回 True 表示接收该事件。
                           None 表示接收所有事件。
                           常用示例：
                               lambda e: e.to_agent == my_agent_id   # 只收发给我的
                               lambda e: e.type == EventType.DONE     # 只收 done 事件
            maxsize:       订阅者队列容量上限，默认 256。

        Returns:
            Subscription 对象，建议用 async with 使用以自动注销。

        Note:
            如果 subscriber_id 已存在，旧的订阅会被覆盖并 log warning。
        """
        if subscriber_id in self._subscribers:
            logger.warning(
                "EventBus: subscriber_id already exists, overwriting | id={}",
                subscriber_id,
            )
            # 先注销旧的
            del self._subscribers[subscriber_id]

        queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers[subscriber_id] = (queue, filter_fn)
        logger.debug("EventBus: subscribed | id={} total={}", subscriber_id, len(self._subscribers))
        return Subscription(bus=self, subscriber_id=subscriber_id, queue=queue)

    def unsubscribe(self, subscriber_id: str) -> None:
        """
        注销订阅者，清理其队列。

        Args:
            subscriber_id: 订阅者 ID，不存在时静默忽略。
        """
        if subscriber_id in self._subscribers:
            del self._subscribers[subscriber_id]
            logger.debug(
                "EventBus: unsubscribed | id={} remaining={}",
                subscriber_id, len(self._subscribers),
            )

    async def publish(self, event: AgentEvent) -> None:
        """
        广播事件给所有匹配的订阅者（fan-out）。

        对每个订阅者：
          1. 若有 filter_fn，先过滤，不匹配则跳过
          2. 若 Queue 未满，直接 put_nowait
          3. 若 Queue 已满（背压），丢弃最旧一条再放入新事件，并 log warning

        Args:
            event: 要广播的事件。
        """
        if not self._subscribers:
            return

        # 遍历所有订阅者
        # 注意：用 list() 复制 keys，防止 publish 过程中有订阅者注销导致字典大小变化
        for sub_id in list(self._subscribers.keys()):
            entry = self._subscribers.get(sub_id)
            if entry is None:
                # 迭代过程中被注销，跳过
                continue

            queue, filter_fn = entry

            # 过滤检查
            if filter_fn is not None:
                try:
                    if not filter_fn(event):
                        continue
                except Exception as e:
                    logger.warning(
                        "EventBus: filter_fn raised exception, skipping | sub_id={} err={}",
                        sub_id, e,
                    )
                    continue

            # 放入队列
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # 背压：队列满了，丢弃最旧的一条，腾出空间
                try:
                    dropped = queue.get_nowait()
                    logger.warning(
                        "EventBus: queue full, dropping oldest event | sub_id={} dropped_type={}",
                        sub_id, dropped.type,
                    )
                    queue.put_nowait(event)
                except Exception as e:
                    logger.error(
                        "EventBus: failed to handle full queue | sub_id={} err={}",
                        sub_id, e,
                    )

    def subscriber_count(self) -> int:
        """返回当前订阅者数量，用于监控和调试。"""
        return len(self._subscribers)

    def subscriber_ids(self) -> list[str]:
        """返回当前所有订阅者 ID 列表，用于调试。"""
        return list(self._subscribers.keys())
