"""
tasks/agent.py — 后台 Agent 任务的状态数据结构和 Session 级注册表。

设计背景
────────────────────────────────────────────────────────────────────────────
Claude Code 的 LocalAgentTask（第五章）将每个后台 Agent 作为独立的 TaskState
记录在 AppState.tasks 中统一管理。本模块对齐这一设计，为 ccserver 的后台 Agent
提供：
  - AgentTaskState：单个后台 Agent 的状态记录
  - AgentTaskRegistry：Session 级的注册表
  - generate_agent_id()：生成唯一 agent 任务 ID

Agent 任务的生命周期通过 BackgroundAgentHandle 管理：
  spawn_background() → BackgroundAgentHandle（含 agent_task_id）
                    → emit_task_started → 后台协程运行
                    → outbox 监听 → emit_task_done

与 ShellTaskState 的区别
────────────────────────────────────────────────────────────────────────────
ShellTaskState  绑定操作系统进程，有 pid / exit_code，proc 可 kill()
AgentTaskState  绑定 Agent 实例，有 prompt，无进程生命周期概念，
                取消通过 asyncio.CancelledError 实现
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from loguru import logger


# ─── 常量 ─────────────────────────────────────────────────────────────────────

# Agent 任务 ID 的前缀，与 Claude Code 保持一致（a = agent）
AGENT_TASK_PREFIX = "a"


# ─── 状态 ─────────────────────────────────────────────────────────────────────

class AgentTaskStatus:
    """Agent 后台任务的状态枚举。"""
    PENDING: str = "pending"
    RUNNING: str = "running"
    COMPLETED: str = "completed"
    FAILED: str = "failed"
    CANCELLED: str = "cancelled"


# ─── ID 生成 ──────────────────────────────────────────────────────────────────

def generate_agent_id() -> str:
    """
    生成唯一的 Agent 任务 ID。

    格式："a" + uuid 的前 8 位。
    与 ShellTaskState 的 "b" 前缀共同构成完整的任务 ID 空间。

    Returns:
        形如 "a3f2a1c0" 的任务 ID。
    """
    short_uuid = uuid.uuid4().hex[:8]
    task_id = f"{AGENT_TASK_PREFIX}{short_uuid}"
    logger.debug("AgentTaskId generated | id={}", task_id)
    return task_id


# ─── AgentTaskState ───────────────────────────────────────────────────────────

@dataclass
class AgentTaskState:
    """
    单个后台 Agent 任务的状态记录。

    Attributes
    ──────────
    id : str
        唯一标识，格式为 "a" + uuid 前 8 位（如 "a3f2a1c0"）。
        前缀 "a" 便于日志分析和客户端快速识别为 Agent 任务。

    agent_id : str
        Agent 实例的内部 ID（由 AgentContext.agent_id 生成）。

    agent_name : str | None
        Agent 的名称（如 "researcher", "coder"）。

    description : str
        人类可读的简短描述，通常是 Agent 的 system prompt 或任务说明。
        用于 UI 渲染（TUI 后台任务列表）。

    prompt : str
        Agent 启动时的用户 prompt（第一条消息内容）。

    status : str
        当前状态，取值见 AgentTaskStatus：
        - pending   : 已创建，协程尚未开始
        - running   : 协程运行中
        - completed: 正常结束（无错误）
        - failed    : 异常结束（抛出未捕获异常）
        - cancelled : 被 cancel() 主动终止

    start_time : datetime | None
        Agent 协程实际开始运行的时间。

    end_time : datetime | None
        Agent 协程结束（completed/failed/cancelled）的时间。

    result : str | None
        Agent 的最终输出内容（done 事件携带的 content）。

    error : str | None
        Agent 失败时的错误信息（error 事件携带的 error 字段）。

    inbox : asyncio.Queue
        外部向此 Agent 发送消息的队列。
        引用 BackgroundAgentHandle.inbox，供外部注入消息。

    outbox : asyncio.Queue
        此 Agent 产出的事件队列。
        引用 BackgroundAgentHandle.outbox。
        外部（spawn_background 的调用方）通过监听此队列获取 Agent 事件。

    agent_task_id : str
        与 id 相同，供外部快速访问。
    """

    id: str
    agent_id: str
    agent_name: str | None = None
    description: str = ""
    prompt: str = ""

    # ── 状态 ────────────────────────────────────────────────────────────────
    status: str = AgentTaskStatus.PENDING

    # ── 时间戳 ─────────────────────────────────────────────────────────────
    start_time: datetime | None = None
    end_time: datetime | None = None

    # ── 结果 ────────────────────────────────────────────────────────────────
    result: str | None = None
    error: str | None = None

    # ── 事件队列（引用 BackgroundAgentHandle 的队列）───────────────────────
    inbox: asyncio.Queue = field(default_factory=asyncio.Queue)
    outbox: asyncio.Queue = field(default_factory=asyncio.Queue)

    def __post_init__(self):
        # agent_task_id 作为 id 的别名，方便序列化
        self.agent_task_id = self.id

    # ── 只读属性 ────────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self.status == AgentTaskStatus.RUNNING

    @property
    def is_done(self) -> bool:
        return self.status in (
            AgentTaskStatus.COMPLETED,
            AgentTaskStatus.FAILED,
            AgentTaskStatus.CANCELLED,
        )

    # ── 状态变更 ──────────────────────────────────────────────────────────

    def mark_running(self) -> None:
        """标记为 running。"""
        assert self.status == AgentTaskStatus.PENDING, (
            f"mark_running: task {self.id} must be pending, but is {self.status}"
        )
        self.status = AgentTaskStatus.RUNNING
        self.start_time = datetime.now(timezone.utc)
        logger.debug("AgentTask running | id={} agent_id={}", self.id, self.agent_id[:8])

    def mark_completed(self, result: str = "") -> None:
        """标记为 completed（正常结束）。"""
        assert self.status == AgentTaskStatus.RUNNING, (
            f"mark_completed: task {self.id} must be running, but is {self.status}"
        )
        self.status = AgentTaskStatus.COMPLETED
        self.result = result
        self.end_time = datetime.now(timezone.utc)
        logger.info(
            "AgentTask completed | id={} agent_id={} result_len={}",
            self.id, self.agent_id[:8], len(result)
        )

    def mark_failed(self, error: str) -> None:
        """标记为 failed（异常结束）。"""
        assert self.status == AgentTaskStatus.RUNNING, (
            f"mark_failed: task {self.id} must be running, but is {self.status}"
        )
        self.status = AgentTaskStatus.FAILED
        self.error = error
        self.end_time = datetime.now(timezone.utc)
        logger.warning(
            "AgentTask failed | id={} agent_id={} error={}",
            self.id, self.agent_id[:8], error[:100]
        )

    def mark_cancelled(self) -> None:
        """标记为 cancelled（被 cancel() 主动终止）。"""
        assert self.status == AgentTaskStatus.RUNNING, (
            f"mark_cancelled: task {self.id} must be running, but is {self.status}"
        )
        self.status = AgentTaskStatus.CANCELLED
        self.end_time = datetime.now(timezone.utc)
        logger.info("AgentTask cancelled | id={} agent_id={}", self.id, self.agent_id[:8])

    # ── 序列化 ─────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """
        序列化为字典，供 StorageAdapter 或 HTTP API 返回。
        """
        return {
            "id": self.id,
            "agent_task_id": self.agent_task_id,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "description": self.description,
            "prompt": self.prompt,
            "status": self.status,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "result": self.result,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentTaskState":
        """从字典反序列化。inbox/outbox 无法恢复，设为空队列。"""
        state = cls(
            id=data["id"],
            agent_id=data["agent_id"],
            agent_name=data.get("agent_name"),
            description=data.get("description", ""),
            prompt=data.get("prompt", ""),
            status=data.get("status", AgentTaskStatus.PENDING),
        )
        if data.get("start_time"):
            state.start_time = datetime.fromisoformat(data["start_time"])
        if data.get("end_time"):
            state.end_time = datetime.fromisoformat(data["end_time"])
        state.result = data.get("result")
        state.error = data.get("error")
        return state


# ─── AgentTaskRegistry ─────────────────────────────────────────────────────────

class AgentTaskRegistry:
    """
    Session 级别的后台 Agent 任务注册表。

    所有 spawn_background() 启动的 Agent 均注册于此。
    与 ShellTaskRegistry 并列，构成 Session 的完整任务视图。

    Attributes
    ──────────
    _tasks : dict[str, AgentTaskState]
        agent_task_id → AgentTaskState 的映射表。
    """

    def __init__(self):
        self._tasks: dict[str, AgentTaskState] = {}
        logger.debug("AgentTaskRegistry initialized")

    def register(self, state: AgentTaskState) -> None:
        """注册新的 AgentTaskState。"""
        if state.id in self._tasks:
            logger.error(
                "AgentTaskRegistry: duplicate task_id={} — cannot register twice",
                state.id
            )
            raise ValueError(f"task_id {state.id} already registered")
        self._tasks[state.id] = state
        logger.debug(
            "AgentTaskRegistry: registered | id={} agent_id={}",
            state.id, state.agent_id[:8]
        )

    def get(self, task_id: str) -> Optional[AgentTaskState]:
        """根据 task_id 查询 Agent 任务状态。"""
        return self._tasks.get(task_id)

    def get_by_agent_id(self, agent_id: str) -> Optional[AgentTaskState]:
        """根据 agent_id 查询（agent_id 可能与 task_id 不同）。"""
        for state in self._tasks.values():
            if state.agent_id == agent_id:
                return state
        return None

    def list_all(self) -> list[AgentTaskState]:
        """返回所有任务。"""
        return list(self._tasks.values())

    def list_running(self) -> list[AgentTaskState]:
        """返回所有 running 状态的任务。"""
        return [t for t in self._tasks.values() if t.is_running]

    def list_done(self) -> list[AgentTaskState]:
        """返回所有已终结的任务。"""
        return [t for t in self._tasks.values() if t.is_done]

    def count(self) -> int:
        return len(self._tasks)

    def count_running(self) -> int:
        return len([t for t in self._tasks.values() if t.is_running])

    def evict(self, task_id: str) -> bool:
        """驱逐已完成的任务。"""
        state = self._tasks.get(task_id)
        if state is None:
            return False
        if not state.is_done:
            logger.warning(
                "AgentTaskRegistry: evict refused — task {} is {} (not done)",
                task_id, state.status
            )
            return False
        del self._tasks[task_id]
        logger.debug("AgentTaskRegistry: evicted | id={}", task_id)
        return True

    def evict_done_tasks(self) -> int:
        """批量驱逐所有已完成的任务。"""
        done_ids = [t.id for t in self._tasks.values() if t.is_done]
        for tid in done_ids:
            self.evict(tid)
        if done_ids:
            logger.info("AgentTaskRegistry: evicted {} done tasks", len(done_ids))
        return len(done_ids)

    def summary(self) -> dict:
        """返回全局统计摘要。"""
        total = self.count()
        running = self.count_running()
        return {
            "total": total,
            "running": running,
            "completed": sum(1 for t in self._tasks.values() if t.status == AgentTaskStatus.COMPLETED),
            "failed": sum(1 for t in self._tasks.values() if t.status == AgentTaskStatus.FAILED),
            "cancelled": sum(1 for t in self._tasks.values() if t.status == AgentTaskStatus.CANCELLED),
        }
