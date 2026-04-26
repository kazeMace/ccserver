"""
event_bus — Session 级 Agent 事件总线（含背压保护）。

设计目标
────────
- 任意数量的 Agent 向总线 publish 事件（发布者）
- 任意数量的观察者独立订阅总线（订阅者），互不影响
- 支持按 to_agent / event type 过滤，实现点对点或广播
- Queue 消费互不干扰：每个订阅者有独立的副本队列
- 背压保护：Queue 满时优先落盘，其次告警，最后才丢弃

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
import json
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
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


# ── JSON 序列化辅助 ───────────────────────────────────────────────────────────

def _event_json_default(obj):
    """
    AgentEvent JSON 序列化的 fallback。

    处理优先级：
      1. datetime → ISO 格式字符串
      2. Path → str
      3. 其他 → 记录 warning，返回 "<unserializable:类型名>" 标记
    """
    from datetime import datetime
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    type_name = f"{type(obj).__module__}.{type(obj).__name__}"
    logger.warning("EventBus JSON fallback | type={}", type_name)
    return f"<unserializable:{type_name}>"


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

    def to_dict(self) -> dict:
        """
        将 AgentEvent 序列化为字典，用于 JSON 持久化。

        Returns:
            包含所有字段的字典。
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AgentEvent":
        """
        从字典反序列化 AgentEvent。

        Args:
            data: to_dict() 产生的字典。

        Returns:
            新的 AgentEvent 实例。
        """
        return cls(
            type=data["type"],
            agent_id=data["agent_id"],
            session_id=data["session_id"],
            payload=data.get("payload", {}),
            sender_type=data.get("sender_type", SenderType.AGENT),
            to_agent=data.get("to_agent"),
            event_id=data.get("event_id", uuid.uuid4().hex[:12]),
            ts=data.get("ts", time.monotonic()),
        )


# ── OverflowBuffer（磁盘溢出缓冲区）────────────────────────────────────────────

class OverflowBuffer:
    """
    订阅者级别的磁盘溢出缓冲区。

    当 asyncio.Queue 满时，事件被追加到 JSONL 文件而不是丢弃。
    当消费者恢复后，事件从文件重放回队列。

    文件格式：每行一个 JSON 对象（JSONL），便于追加和逐行读取。

    Attributes
    ──────────
    _file_path : Path
        溢出文件路径，格式：{overflow_dir}/{subscriber_id}.jsonl

    _max_file_size : int
        单个订阅者溢出文件的最大字节数，默认 10MB。
        超过此限制后，append 返回 False，触发最终丢弃。

    _overflow_count : int
        当前溢出缓冲区中的事件数（内存计数，用于监控）。

    _lock : asyncio.Lock
        保护文件操作的异步锁。
    """

    # 单个订阅者溢出文件的最大大小（10MB）
    DEFAULT_MAX_FILE_SIZE = 10 * 1024 * 1024

    def __init__(
        self,
        subscriber_id: str,
        overflow_dir: Path,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    ):
        self._subscriber_id = subscriber_id
        self._file_path = overflow_dir / f"{subscriber_id}.jsonl"
        self._max_file_size = max_file_size
        self._overflow_count = 0
        self._lock = asyncio.Lock()
        logger.debug(
            "OverflowBuffer: initialized | sub_id={} path={}",
            subscriber_id, self._file_path,
        )

    async def append(self, event: AgentEvent) -> bool:
        """
        将事件追加到溢出文件。

        Args:
            event: 要持久化的事件。

        Returns:
            True 表示追加成功，False 表示溢出文件已满或写入失败。
        """
        async with self._lock:
            # 检查文件大小限制
            if self._file_path.exists():
                try:
                    current_size = self._file_path.stat().st_size
                    if current_size >= self._max_file_size:
                        logger.warning(
                            "OverflowBuffer: file size limit reached | "
                            "sub_id={} size={}B limit={}B",
                            self._subscriber_id, current_size, self._max_file_size,
                        )
                        return False
                except OSError as exc:
                    logger.exception(
                        "OverflowBuffer: stat failed | sub_id={} err={}",
                        self._subscriber_id, exc,
                    )
                    return False

            # 序列化并追加
            try:
                line = json.dumps(event.to_dict(), default=_event_json_default) + "\n"
                # 使用 'a' 模式追加，配合 flush 保证数据落盘
                with open(self._file_path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
                self._overflow_count += 1
                logger.debug(
                    "OverflowBuffer: event appended | sub_id={} type={} count={}",
                    self._subscriber_id, event.type, self._overflow_count,
                )
                return True
            except Exception as exc:
                logger.exception(
                    "OverflowBuffer: append failed | sub_id={} type={} err={}",
                    self._subscriber_id, event.type, exc,
                )
                return False

    async def replay(self, queue: asyncio.Queue, batch_size: int = 10) -> int:
        """
        从溢出文件重放事件到队列。

        读取文件前部的 batch_size 行，尽可能放入队列，然后从文件中删除已重放的行。
        如果队列空间不足，只重放能放入的数量。

        Args:
            queue: 目标队列。
            batch_size: 每批最大重放数量。

        Returns:
            实际重放的事件数量。
        """
        async with self._lock:
            if not self._file_path.exists():
                return 0

            try:
                with open(self._file_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError as exc:
                logger.exception(
                    "OverflowBuffer: read failed | sub_id={} err={}",
                    self._subscriber_id, exc,
                )
                return 0

            if not lines:
                return 0

            # 计算队列可用空间
            free_slots = queue.maxsize - queue.qsize()
            if free_slots <= 0:
                return 0

            to_replay = min(len(lines), batch_size, free_slots)
            replayed = 0

            for i in range(to_replay):
                line = lines[i].strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    event = AgentEvent.from_dict(data)
                    queue.put_nowait(event)
                    replayed += 1
                except Exception as exc:
                    logger.warning(
                        "OverflowBuffer: replay parse failed | sub_id={} line={!r} err={}",
                        self._subscriber_id, line[:80], exc,
                    )
                    # 跳过损坏的行，继续处理下一行
                    continue

            # 将未重放的行写回文件
            remaining = lines[replayed:]
            if remaining:
                try:
                    with open(self._file_path, "w", encoding="utf-8") as f:
                        f.writelines(remaining)
                except OSError as exc:
                    logger.exception(
                        "OverflowBuffer: rewrite failed | sub_id={} err={}",
                        self._subscriber_id, exc,
                    )
                    # 文件可能损坏，但已重放的事件已安全入队
            else:
                # 全部重放完毕，删除文件
                try:
                    self._file_path.unlink()
                except OSError as exc:
                    logger.warning(
                        "OverflowBuffer: unlink failed | sub_id={} err={}",
                        self._subscriber_id, exc,
                    )

            self._overflow_count = max(0, self._overflow_count - replayed)
            if replayed > 0:
                logger.debug(
                    "OverflowBuffer: replayed {} events | sub_id={} remaining={}",
                    replayed, self._subscriber_id, len(remaining) if remaining else 0,
                )
            return replayed

    def clear(self) -> None:
        """
        清理溢出文件。subscriber 注销时调用。
        """
        if self._file_path.exists():
            try:
                self._file_path.unlink()
                logger.debug(
                    "OverflowBuffer: cleared | sub_id={}", self._subscriber_id,
                )
            except OSError as exc:
                logger.warning(
                    "OverflowBuffer: clear failed | sub_id={} err={}",
                    self._subscriber_id, exc,
                )
        self._overflow_count = 0


# ── BackpressureMonitor（背压监控器）───────────────────────────────────────────

@dataclass
class BackpressureMetrics:
    """
    背压监控指标快照。

    Attributes
    ──────────
    queue_size : int
        当前队列大小。
    maxsize : int
        队列最大容量。
    fill_ratio : float
        填充比例（0.0 ~ 1.0）。
    events_per_sec : float
        最近时间窗口内的平均入队速率（事件/秒）。
    overflow_count : int
        溢出缓冲区中的事件数。
    alert_level : str
        当前告警级别：normal / warning / critical / emergency。
    """
    queue_size: int
    maxsize: int
    fill_ratio: float
    events_per_sec: float
    overflow_count: int
    alert_level: str


class BackpressureMonitor:
    """
    订阅者级别的背压监控器。

    跟踪队列深度变化，计算堆积速率，触发分级告警。

    告警级别
    ─────────
    - normal    : 一切正常
    - warning   : 队列 >= 70% 或堆积速率 >= 50 evt/s
    - critical  : 队列 >= 90% 或溢出缓冲区激活
    - emergency : 队列满且溢出缓冲区也满（开始丢弃事件）

    去抖：同一级别每 30 秒最多触发一次，避免日志风暴。
    """

    # 告警阈值
    WARNING_RATIO = 0.70
    CRITICAL_RATIO = 0.90
    ACCUMULATION_THRESHOLD = 50.0  # events/sec

    # 去抖间隔（秒）
    ALERT_COOLDOWN = 30.0

    # 速率计算窗口（秒）
    RATE_WINDOW = 5.0

    def __init__(self, subscriber_id: str, maxsize: int):
        self._subscriber_id = subscriber_id
        self._maxsize = maxsize
        # 记录 (timestamp, queue_size) 的历史，用于计算速率
        self._history: deque[tuple[float, int]] = deque()
        self._last_alert_level = "normal"
        self._last_alert_time = 0.0

    def record(self, queue_size: int) -> None:
        """
        记录当前队列状态。

        Args:
            queue_size: 当前队列大小。
        """
        now = time.monotonic()
        self._history.append((now, queue_size))

        # 清理过期的历史记录（超过 RATE_WINDOW 的）
        cutoff = now - self.RATE_WINDOW
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

    def check(self, overflow_active: bool = False, overflow_count: int = 0) -> tuple[str, BackpressureMetrics]:
        """
        检查当前背压状态并返回告警级别和指标。

        Args:
            overflow_active: 溢出缓冲区是否激活（有数据或刚写入）。
            overflow_count: 溢出缓冲区中的事件数。

        Returns:
            (alert_level, metrics) 元组。
        """
        # 获取最新队列大小
        if self._history:
            _, queue_size = self._history[-1]
        else:
            queue_size = 0

        fill_ratio = queue_size / self._maxsize if self._maxsize > 0 else 0.0

        # 计算堆积速率（事件/秒）
        if len(self._history) >= 2:
            time_span = self._history[-1][0] - self._history[0][0]
            event_span = len(self._history)
            events_per_sec = event_span / time_span if time_span > 0 else 0.0
        else:
            events_per_sec = 0.0

        # 确定告警级别
        if fill_ratio >= 1.0 and overflow_active:
            alert_level = "emergency"
        elif fill_ratio >= self.CRITICAL_RATIO or overflow_active:
            alert_level = "critical"
        elif fill_ratio >= self.WARNING_RATIO or events_per_sec >= self.ACCUMULATION_THRESHOLD:
            alert_level = "warning"
        else:
            alert_level = "normal"

        metrics = BackpressureMetrics(
            queue_size=queue_size,
            maxsize=self._maxsize,
            fill_ratio=fill_ratio,
            events_per_sec=events_per_sec,
            overflow_count=overflow_count,
            alert_level=alert_level,
        )

        # 检查是否需要触发告警（级别变化或冷却结束）
        now = time.monotonic()
        should_alert = (
            alert_level != self._last_alert_level
            or (alert_level != "normal" and now - self._last_alert_time >= self.ALERT_COOLDOWN)
        )

        if should_alert:
            self._emit_alert(alert_level, metrics)
            self._last_alert_level = alert_level
            self._last_alert_time = now

        return alert_level, metrics

    def _emit_alert(self, level: str, metrics: BackpressureMetrics) -> None:
        """
        根据级别输出对应的日志。

        Args:
            level: 告警级别。
            metrics: 背压指标。
        """
        msg = (
            f"EventBus backpressure | sub_id={self._subscriber_id} "
            f"level={level} queue={metrics.queue_size}/{metrics.maxsize} "
            f"fill={metrics.fill_ratio:.1%} rate={metrics.events_per_sec:.1f}evt/s "
            f"overflow={metrics.overflow_count}"
        )

        if level == "emergency":
            logger.critical(msg)
        elif level == "critical":
            logger.error(msg)
        elif level == "warning":
            logger.warning(msg)
        # normal 不输出


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

    def __init__(
        self,
        bus: "EventBus",
        subscriber_id: str,
        queue: asyncio.Queue,
        overflow: Optional[OverflowBuffer] = None,
    ):
        # 持有总线引用，用于退出时注销
        self._bus = bus
        self._subscriber_id = subscriber_id
        self._queue = queue
        self._overflow = overflow

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

        优先处理溢出缓冲区中的事件：如果磁盘上有积压且队列有空间，
        先将溢出事件重放回队列，再消费。
        """
        # 先尝试从溢出缓冲区重放事件
        if self._overflow is not None:
            replayed = await self._overflow.replay(self._queue)
            if replayed > 0 and not self._queue.empty():
                return await self._queue.get()

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
        # 先尝试从溢出缓冲区重放
        if self._overflow is not None:
            await self._overflow.replay(self._queue)
            if not self._queue.empty():
                return await self._queue.get()

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

    背压处理（优先级从高到低）：
        1. 优先放入内存队列（asyncio.Queue）
        2. 队列满时，将事件追加到磁盘溢出缓冲区（OverflowBuffer，JSONL 文件）
        3. 磁盘溢出也满时，发出 emergency 告警并丢弃事件（最后手段）
        4. 持续监控队列深度和堆积速率，触发分级告警

    线程安全：
        本类仅用于 asyncio 协程，不支持多线程并发访问。
    """

    # 订阅者 Queue 的默认最大容量
    DEFAULT_QUEUE_MAXSIZE = 256

    def __init__(self, overflow_dir: Optional[Path] = None):
        """
        初始化 EventBus。

        Args:
            overflow_dir: 可选的溢出目录路径。如果提供，Queue 满时事件会被
                         持久化到该目录下的 JSONL 文件；如果为 None，Queue 满时
                         直接丢弃最旧事件（与旧行为一致）。
        """
        # subscriber_id -> (asyncio.Queue, filter_fn, overflow, monitor)
        # filter_fn: 接收 AgentEvent 返回 bool，None 表示接收所有事件
        self._subscribers: dict[
            str,
            tuple[asyncio.Queue, Optional[Callable], Optional[OverflowBuffer], BackpressureMonitor],
        ] = {}
        self._overflow_dir = overflow_dir
        if overflow_dir is not None:
            overflow_dir.mkdir(parents=True, exist_ok=True)
            logger.debug("EventBus: overflow enabled | dir={}", overflow_dir)

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
            # 先注销旧的，清理资源
            self.unsubscribe(subscriber_id)

        queue = asyncio.Queue(maxsize=maxsize)

        # 创建溢出缓冲区（如果配置了 overflow_dir）
        overflow: Optional[OverflowBuffer] = None
        if self._overflow_dir is not None:
            overflow = OverflowBuffer(
                subscriber_id=subscriber_id,
                overflow_dir=self._overflow_dir,
            )

        # 创建背压监控器
        monitor = BackpressureMonitor(
            subscriber_id=subscriber_id,
            maxsize=maxsize,
        )

        self._subscribers[subscriber_id] = (queue, filter_fn, overflow, monitor)
        logger.debug(
            "EventBus: subscribed | id={} total={} maxsize={} overflow={}",
            subscriber_id, len(self._subscribers), maxsize,
            "enabled" if overflow else "disabled",
        )
        return Subscription(
            bus=self,
            subscriber_id=subscriber_id,
            queue=queue,
            overflow=overflow,
        )

    def unsubscribe(self, subscriber_id: str) -> None:
        """
        注销订阅者，清理其队列和溢出文件。

        Args:
            subscriber_id: 订阅者 ID，不存在时静默忽略。
        """
        entry = self._subscribers.get(subscriber_id)
        if entry is None:
            return

        queue, filter_fn, overflow, monitor = entry
        del self._subscribers[subscriber_id]

        # 清理溢出文件
        if overflow is not None:
            overflow.clear()

        logger.debug(
            "EventBus: unsubscribed | id={} remaining={}",
            subscriber_id, len(self._subscribers),
        )

    async def publish(self, event: AgentEvent) -> None:
        """
        广播事件给所有匹配的订阅者（fan-out）。

        对每个订阅者：
          1. 若有 filter_fn，先过滤，不匹配则跳过
          2. 若 Queue 未满，直接 put_nowait（内存优先）
          3. 若 Queue 已满：
             a. 若配置了 overflow_dir，尝试写入磁盘溢出缓冲区
             b. 磁盘溢出也满时，发出 emergency 告警并丢弃事件
          4. 记录监控指标，触发分级告警

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

            queue, filter_fn, overflow, monitor = entry

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

            # 尝试放入队列
            dropped = False
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # 队列满了
                if overflow is not None:
                    # 优先尝试写入磁盘溢出缓冲区（新行为：不丢弃旧事件）
                    ok = await overflow.append(event)
                    if not ok:
                        # 溢出缓冲区也满了，最后手段：丢弃新事件
                        dropped = True
                        logger.critical(
                            "EventBus: EMERGENCY — queue and overflow both full, "
                            "dropping event | sub_id={} type={}",
                            sub_id, event.type,
                        )
                else:
                    # 无溢出目录配置，退化为旧行为（丢弃最旧，放入最新）
                    try:
                        dropped_event = queue.get_nowait()
                        logger.warning(
                            "EventBus: queue full, dropping oldest event | "
                            "sub_id={} dropped_type={}",
                            sub_id, dropped_event.type,
                        )
                        queue.put_nowait(event)
                    except Exception as e:
                        logger.error(
                            "EventBus: failed to handle full queue | sub_id={} err={}",
                            sub_id, e,
                        )

            # 记录监控指标
            monitor.record(queue.qsize())
            overflow_active = (overflow is not None and overflow._overflow_count > 0)
            alert_level, metrics = monitor.check(
                overflow_active=overflow_active,
                overflow_count=overflow._overflow_count if overflow else 0,
            )

            # 如果发生了丢弃，强制提升为 emergency
            if dropped and alert_level != "emergency":
                logger.critical(
                    "EventBus: backpressure escalated to emergency | "
                    "sub_id={} queue={}/{} overflow={}",
                    sub_id, metrics.queue_size, metrics.maxsize,
                    metrics.overflow_count,
                )

    def subscriber_count(self) -> int:
        """返回当前订阅者数量，用于监控和调试。"""
        return len(self._subscribers)

    def subscriber_ids(self) -> list[str]:
        """返回当前所有订阅者 ID 列表，用于调试。"""
        return list(self._subscribers.keys())

    def get_metrics(self, subscriber_id: str) -> Optional[BackpressureMetrics]:
        """
        获取指定订阅者的背压指标。

        Args:
            subscriber_id: 订阅者 ID。

        Returns:
            BackpressureMetrics，不存在返回 None。
        """
        entry = self._subscribers.get(subscriber_id)
        if entry is None:
            return None
        queue, filter_fn, overflow, monitor = entry
        overflow_active = (overflow is not None and overflow._overflow_count > 0)
        _, metrics = monitor.check(
            overflow_active=overflow_active,
            overflow_count=overflow._overflow_count if overflow else 0,
        )
        return metrics
