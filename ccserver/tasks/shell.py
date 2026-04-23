"""
tasks/shell.py — 本地 Shell 后台任务的状态数据结构和 Session 级注册表。

设计背景
────────────────────────────────────────────────────────────────────────────
Claude Code 的 LocalShellTask（第五章）将每个后台 bash 命令作为独立的
TaskState 记录在 AppState.tasks 中统一管理，生命周期（创建/轮询/完成/清理）
全部经过同一个注册表。

ccserver 遵循相同的设计：每个通过 BTBash.run_in_background=True 启动的后台
shell 均创建为 ShellTaskState 并注册到 Session.shell_tasks 字典。
外部（emitter / HTTP API / TUI）通过 ShellTaskRegistry 查询任务状态和增量输出。

数据流
────────────────────────────────────────────────────────────────────────────
BTBash.run(run_in_background=True)
    │
    ├── generate_shell_id()              → task_id = "b1"
    ├── ShellTaskState(...)
    ├── Session._shell_tasks[id] = state  ← 注册
    ├── subprocess = asyncio.create_subprocess_shell(...)
    ├── proc_started.set_result(subprocess)
    └── 返回 ToolResult(background_task_id="b1")

Session._shell_tasks[id].proc.wait()  →  进程结束时自动触发完成处理
ShellTaskRegistry.list_all()          →  外部查询接口
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from loguru import logger


# ─── 常量 ─────────────────────────────────────────────────────────────────────

# Shell 任务 ID 的前缀，与 Claude Code 保持一致（b = bash）
SHELL_TASK_PREFIX = "b"

# 任务状态
class TaskStatus:
    PENDING: str = "pending"
    RUNNING: str = "running"
    COMPLETED: str = "completed"
    FAILED: str = "failed"
    KILLED: str = "killed"

# 合法的状态集合（用于 assert 校验）
_VALID_STATUSES: frozenset = frozenset([
    TaskStatus.PENDING,
    TaskStatus.RUNNING,
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.KILLED,
])


# ─── ID 生成 ──────────────────────────────────────────────────────────────────

def generate_shell_id() -> str:
    """
    生成唯一的 Shell 任务 ID。

    格式："b" + uuid 的前 8 位，确保在并发场景下不冲突。
    不使用自增整数是为了避免多进程/多实例场景下的 ID 碰撞。

    Returns:
        形如 "b3f2a1c0" 的任务 ID。
    """
    short_uuid = uuid.uuid4().hex[:8]
    task_id = f"{SHELL_TASK_PREFIX}{short_uuid}"
    logger.debug("ShellTaskId generated | id={}", task_id)
    return task_id


# ─── 类型守卫 ────────────────────────────────────────────────────────────────

def is_shell_task_state(obj: object) -> bool:
    """
    运行时类型守卫，判断一个对象是否是 ShellTaskState。

    等价于 TypeScript 的 type guard:
        export function isLocalShellTask(task: unknown): task is LocalShellTaskState

    Args:
        obj: 待检查的对象。

    Returns:
        True 表示 obj 是 ShellTaskState 实例。
    """
    return isinstance(obj, ShellTaskState)


# ─── ShellTaskState ───────────────────────────────────────────────────────────

def _make_proc_started_future() -> "asyncio.Future[asyncio.subprocess.Process]":
    """
    为 ShellTaskState.proc_started 字段创建 Future。

    使用独立函数而非 lambda，规避 asyncio.get_running_loop() 的 DeprecationWarning。
    详见 field 注释。
    """
    try:
        loop = asyncio.get_running_loop()
        return loop.create_future()
    except RuntimeError:
        # 同步上下文（无运行中事件循环）下，创建临时循环用于构造 future，
        # 之后立即关闭。mark_running 会用真正的 proc future 替换此值。
        loop = asyncio.new_event_loop()
        fut = loop.create_future()
        loop.close()
        return fut


@dataclass
class ShellTaskState:
    """
    单个本地 Shell 后台任务的状态记录。

    对齐 Claude Code LocalShellTaskState 的核心字段。
    每个 run_in_background=True 的 Bash 调用对应一个实例。

    Attributes
    ──────────
    id : str
        唯一标识，格式为 "b" + uuid 前 8 位（如 "b3f2a1c0"）。
        前缀 "b" 便于日志分析和客户端快速识别任务类型。

    command : str
        实际执行的完整 shell 命令。

    description : str
        人类可读的简短描述，来自 BashTool 的 description 参数。
        用于 UI 渲染（如 TUI 的后台任务列表）。

    status : str
        当前状态，取值见 TaskStatus：
        - pending  : 已创建，进程尚未启动
        - running  : 进程运行中
        - completed: 进程正常退出（exit code == 0）
        - failed   : 进程异常退出（exit code != 0）
        - killed   : 被 TaskStop 主动终止

    is_backgrounded : bool
        固定为 True。区分前台任务（直接等待结果）和后台任务（进入注册表）。
        前台任务不使用 ShellTaskState，直接在 BTBash.run() 中 await 即可。

    pid : int | None
        操作系统分配的进程 ID，进程启动后填充。

    proc : asyncio.subprocess.Process | None
        asyncio 子进程引用，用于 wait()、kill() 等操作。
        进程启动后填充，结束后置为 None（避免循环引用导致 GC 延迟）。

    output : str
        到目前为止累积的标准输出 + 标准错误（合并）。
        由 _append_output() 增量追加，供 emit_task_progress 读取。

    output_offset : int
        output 的字节长度（追加前），用于追踪已读取的字节数。
        目前 output 为完整字符串，未来可改为文件追加以支持超大输出。

    exit_code : int | None
        进程退出码，进程结束后填充。

    reason : str | None
        失败/终止原因的描述文本。

    start_time : datetime | None
        进程实际启动的时间（proc_started Future  resolved 时记录）。

    end_time : datetime | None
        进程结束（completed/failed/killed）的时间。

    proc_started : asyncio.Future
        用于延迟填充 pid 和 proc。
        BTBash 在创建 ShellTaskState 后立即返回，proc 稍后通过
        Future 注入，避免 async/await 阻塞工具返回值。
    """

    id: str
    command: str
    description: str = ""

    # ── 状态 ─────────────────────────────────────────────────────────────────
    status: str = TaskStatus.PENDING
    is_backgrounded: bool = True

    # ── 进程信息 ─────────────────────────────────────────────────────────────
    pid: Optional[int] = None
    proc: Optional["asyncio.subprocess.Process"] = None  # type: ignore[name-defined]

    # ── 输出追踪 ─────────────────────────────────────────────────────────────
    output: str = ""
    output_offset: int = 0

    # ── 结果 ─────────────────────────────────────────────────────────────────
    exit_code: Optional[int] = None
    reason: Optional[str] = None

    # ── 时间戳 ───────────────────────────────────────────────────────────────
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    # ── 内部信号 ─────────────────────────────────────────────────────────────
    # 进程对象填充的 Promise，允许 BTBash.run() 同步创建 State 后异步填充 proc。
    # default_factory 使用独立函数，规避 lambda 中 get_running_loop 的警告。
    proc_started: "asyncio.Future[asyncio.subprocess.Process]" = field(
        default_factory=_make_proc_started_future
    )

    # ── 只读属性 ─────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """任务是否处于运行中状态。"""
        return self.status == TaskStatus.RUNNING

    @property
    def is_done(self) -> bool:
        """任务是否已终结（completed / failed / killed 任一）。"""
        return self.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.KILLED,
        )

    @property
    def is_success(self) -> bool:
        """进程是否正常结束（exit_code == 0）。"""
        return self.status == TaskStatus.COMPLETED

    # ── 状态变更 ─────────────────────────────────────────────────────────────

    def mark_running(self, pid: int, proc: "asyncio.subprocess.Process") -> None:
        """
        标记任务为 running，同时填充进程信息。

        Args:
            pid: 操作系统分配的进程 ID。
            proc: asyncio 子进程引用。

        Raises:
            AssertionError: 当前状态不是 pending 时抛出。
        """
        assert self.status == TaskStatus.PENDING, (
            f"mark_running: task {self.id} must be in pending state, "
            f"but is {self.status}"
        )
        self.pid = pid
        self.proc = proc
        self.status = TaskStatus.RUNNING
        self.start_time = datetime.now(timezone.utc)
        logger.debug(
            "ShellTask running | id={} pid={} cmd={!r}",
            self.id, pid, self.command[:80]
        )

    def mark_completed(self, exit_code: int) -> None:
        """
        标记任务为 completed（进程正常退出）。

        Args:
            exit_code: 进程的退出码。

        Raises:
            AssertionError: 当前状态不是 running 时抛出。
        """
        assert self.status == TaskStatus.RUNNING, (
            f"mark_completed: task {self.id} must be in running state, "
            f"but is {self.status}"
        )
        self.exit_code = exit_code
        self.status = TaskStatus.COMPLETED
        self.end_time = datetime.now(timezone.utc)
        self._cleanup_proc()
        logger.debug(
            "ShellTask completed | id={} exit_code={}",
            self.id, exit_code
        )

    def mark_failed(self, exit_code: int, reason: str = "") -> None:
        """
        标记任务为 failed（进程异常退出，即 exit_code != 0）。

        Args:
            exit_code: 进程的退出码。
            reason: 可选的失败原因描述。

        Raises:
            AssertionError: 当前状态不是 running 时抛出。
        """
        assert self.status == TaskStatus.RUNNING, (
            f"mark_failed: task {self.id} must be in running state, "
            f"but is {self.status}"
        )
        self.exit_code = exit_code
        self.reason = reason
        self.status = TaskStatus.FAILED
        self.end_time = datetime.now(timezone.utc)
        self._cleanup_proc()
        logger.debug(
            "ShellTask failed | id={} exit_code={} reason={}",
            self.id, exit_code, reason
        )

    def mark_killed(self, reason: str = "killed by TaskStop") -> None:
        """
        标记任务为 killed（被 TaskStop 主动终止）。

        Args:
            reason: 终止原因的描述。

        Raises:
            AssertionError: 当前状态不是 running 时抛出。
        """
        assert self.status == TaskStatus.RUNNING, (
            f"mark_killed: task {self.id} must be in running state, "
            f"but is {self.status}"
        )
        self.reason = reason
        self.status = TaskStatus.KILLED
        self.end_time = datetime.now(timezone.utc)
        if self.proc is not None:
            self.proc.kill()
        self._cleanup_proc()
        logger.info("ShellTask killed | id={} reason={}", self.id, reason)

    # ── 输出追加 ─────────────────────────────────────────────────────────────

    def append_output(self, chunk: str) -> None:
        """
        追加新的输出片段到 output 字段。

        每次追加前更新 output_offset（追加前的长度），确保外部轮询时
        可以通过 output[self.output_offset:] 获取增量部分。

        注意：output 目前是内存字符串，若单个命令输出超过 ~10MB 应考虑
        改用临时文件存储。output_offset 届时可表示文件字节偏移。

        Args:
            chunk: 新的输出字符串。
        """
        self.output_offset = len(self.output)
        self.output += chunk

    def read_incremental(self) -> str:
        """
        返回自上次读取以来的增量输出，并将 offset 更新到最新位置。

        每次调用后 output_offset 被更新为当前 output 的长度，
        下次调用时自动从新位置开始返回新增内容。

        使用场景：pollTasks 轮询时，调用 read_incremental() 获取本轮增量，
        无需关心具体从哪里开始——offset 会自动推进。

        Returns:
            从 output_offset 位置到末尾的新增内容。
        """
        delta = self.output[self.output_offset :]
        self.output_offset = len(self.output)
        return delta

    # ── 内部清理 ─────────────────────────────────────────────────────────────

    def _cleanup_proc(self) -> None:
        """
        清理进程引用，避免循环引用导致垃圾回收延迟。
        proc 置为 None 后，asyncio 事件循环不再持有引用。
        """
        self.proc = None

    # ── 序列化（供 HTTP API / Storage 使用）─────────────────────────────────

    def to_dict(self) -> dict:
        """
        序列化为字典，供 StorageAdapter 或 HTTP API 返回。

        Returns:
            包含所有公开字段的字典。
        """
        return {
            "id": self.id,
            "type": "local_bash",
            "command": self.command,
            "description": self.description,
            "status": self.status,
            "is_backgrounded": self.is_backgrounded,
            "pid": self.pid,
            "output": self.output,
            "exit_code": self.exit_code,
            "reason": self.reason,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ShellTaskState":
        """
        从字典反序列化，构造 ShellTaskState。

        主要用于 Storage 持久化后恢复任务状态。
        注意：from_dict 恢复的任务 proc=None，无法再调用 kill()/wait() 等
        进程操作，只能用于展示历史状态。

        Args:
            data: 包含 ShellTaskState 字段的字典。

        Returns:
            新的 ShellTaskState 实例（proc 字段为 None）。
        """
        state = cls(
            id=data["id"],
            command=data["command"],
            description=data.get("description", ""),
            status=data.get("status", TaskStatus.PENDING),
            is_backgrounded=data.get("is_backgrounded", True),
            pid=data.get("pid"),
            output=data.get("output", ""),
            output_offset=data.get("output_offset", 0),
            exit_code=data.get("exit_code"),
            reason=data.get("reason"),
        )
        if data.get("start_time"):
            state.start_time = datetime.fromisoformat(data["start_time"])
        if data.get("end_time"):
            state.end_time = datetime.fromisoformat(data["end_time"])
        return state


# ─── ShellTaskRegistry ────────────────────────────────────────────────────────

class ShellTaskRegistry:
    """
    Session 级别的后台 Shell 任务注册表。

    对齐 Claude Code 的 AppState.tasks 注册机制：
    所有 run_in_background=True 的 Bash 调用均注册于此。
    外部系统（SSE emitter / WebSocket / HTTP API）通过 Registry 查询任务状态。

    线程安全：asyncio 单线程，无需额外锁。
    所有修改均在事件循环内完成，不存在竞态条件。

    Attributes
    ──────────
    _tasks : dict[str, ShellTaskState]
        task_id → ShellTaskState 的映射表。
        仅包含当前存活的（未 evict）任务。

    _evicted : set[str]
        已终结并被清理的任务 ID 集合。
        用于防止误用已 evict 的 task_id。

    使用方式
    ──────────
    # 注册新任务（BTBash 调用）
    task = ShellTaskState(id=generate_shell_id(), command=cmd)
    registry.register(task)

    # 查询任务（外部消费者）
    task = registry.get("b3f2a1c0")
    if task and task.is_running:
        print(task.output)

    # 终止任务（TaskStop 调用）
    registry.kill("b3f2a1c0", reason="user requested")
    """

    def __init__(self):
        # 任务 ID → ShellTaskState
        self._tasks: dict[str, ShellTaskState] = {}
        # 已 evict 的任务 ID 集合
        self._evicted: set[str] = set()
        logger.debug("ShellTaskRegistry initialized")

    # ── 注册 ────────────────────────────────────────────────────────────────

    def register(self, task: ShellTaskState) -> None:
        """
        将新的 ShellTaskState 注册到注册表。

        Args:
            task: 待注册的任务状态对象。

        Raises:
            ValueError: task_id 已在注册表中（防止重复注册）。
        """
        if task.id in self._tasks:
            logger.error(
                "ShellTaskRegistry: duplicate task_id={} — cannot register twice",
                task.id
            )
            raise ValueError(f"task_id {task.id} already registered")
        self._tasks[task.id] = task
        logger.debug(
            "ShellTaskRegistry: registered | id={} cmd={!r}",
            task.id, task.command[:60]
        )

    # ── 查询 ────────────────────────────────────────────────────────────────

    def get(self, task_id: str) -> Optional[ShellTaskState]:
        """
        根据 task_id 查询任务状态。

        Args:
            task_id: 任务 ID（格式为 "b" + uuid 前 8 位）。

        Returns:
            对应的 ShellTaskState，若不存在或已 evict 返回 None。
        """
        return self._tasks.get(task_id)

    def list_all(self) -> list[ShellTaskState]:
        """
        返回所有任务（按注册顺序）。

        Returns:
            ShellTaskState 列表。
        """
        return list(self._tasks.values())

    def list_running(self) -> list[ShellTaskState]:
        """
        返回所有处于 running 状态的任务。

        用于 pollTasks 轮询需要关注哪些任务。

        Returns:
            running 状态的任务列表。
        """
        return [t for t in self._tasks.values() if t.is_running]

    def list_done(self) -> list[ShellTaskState]:
        """
        返回所有已终结的任务（completed / failed / killed）。

        Returns:
            已终结任务列表。
        """
        return [t for t in self._tasks.values() if t.is_done]

    # ── 终止 ────────────────────────────────────────────────────────────────

    def kill(self, task_id: str, reason: str = "") -> bool:
        """
        主动终止指定的后台任务。

        通过 proc.kill() 发送 SIGKILL，进程立即终止。
        若任务已终结或不存在，返回 False。

        Args:
            task_id: 要终止的任务 ID。
            reason: 终止原因（写入 reason 字段，供日志和事件使用）。

        Returns:
            True 表示终止成功（task_id 存在且状态为 running）。
            False 表示任务不存在或已终结。
        """
        task = self._tasks.get(task_id)
        if task is None:
            logger.warning(
                "ShellTaskRegistry: kill failed — task {} not found", task_id
            )
            return False
        if not task.is_running:
            logger.warning(
                "ShellTaskRegistry: kill skipped — task {} is {} (not running)",
                task_id, task.status
            )
            return False
        task.mark_killed(reason=reason)
        return True

    # ── 清理 ────────────────────────────────────────────────────────────────

    def evict(self, task_id: str) -> bool:
        """
        将已完成的任务从注册表中驱逐（evict）。

        evict 后任务不再出现在 list_all() 中，但 to_dict() 保留历史记录。
        这与 Claude Code 的 evictTerminalTask() 行为一致：
        任务完成后保留状态一段时间，然后从内存中清除。

        Args:
            task_id: 要驱逐的任务 ID。

        Returns:
            True 表示驱逐成功，False 表示任务不存在或不在 done 状态。
        """
        task = self._tasks.get(task_id)
        if task is None:
            logger.debug(
                "ShellTaskRegistry: evict skipped — task {} not found", task_id
            )
            return False
        if not task.is_done:
            logger.warning(
                "ShellTaskRegistry: evict refused — task {} is {} (not done)",
                task_id, task.status
            )
            return False
        del self._tasks[task_id]
        self._evicted.add(task_id)
        logger.debug("ShellTaskRegistry: evicted | id={}", task_id)
        return True

    def evict_done_tasks(self) -> int:
        """
        驱逐所有已终结的任务。

        批量清理，用于 Session 结束时释放资源。

        Returns:
            被驱逐的任务数量。
        """
        done_ids = [t.id for t in self._tasks.values() if t.is_done]
        for tid in done_ids:
            self.evict(tid)
        if done_ids:
            logger.info(
                "ShellTaskRegistry: evicted {} done tasks", len(done_ids)
            )
        return len(done_ids)

    # ── 统计 ────────────────────────────────────────────────────────────────

    def count(self) -> int:
        """返回当前注册的任务总数（不含 evict）。"""
        return len(self._tasks)

    def count_running(self) -> int:
        """返回当前 running 状态的任务数。"""
        return len([t for t in self._tasks.values() if t.is_running])

    def summary(self) -> dict:
        """
        返回注册表的全局统计摘要。

        Returns:
            包含各状态计数的字典。
        """
        total = self.count()
        running = self.count_running()
        return {
            "total": total,
            "running": running,
            "completed": sum(1 for t in self._tasks.values() if t.status == TaskStatus.COMPLETED),
            "failed": sum(1 for t in self._tasks.values() if t.status == TaskStatus.FAILED),
            "killed": sum(1 for t in self._tasks.values() if t.status == TaskStatus.KILLED),
            "evicted": len(self._evicted),
        }
