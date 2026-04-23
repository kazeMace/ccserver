"""
task_manager — Session 级别的任务管理器。

LLM 通过 TaskCreate/TaskUpdate/TaskGet/TaskList 工具操作任务，
跟踪复杂多步骤任务的执行状态。

与原来的 TodoManager 的区别：
  - Todo 是替换式全量更新（每次调用覆盖整个列表）
  - Task 是增量式操作（独立创建、独立更新，保留历史）
  - Task 有自增 ID，支持按 ID 精确操作
  - Task 支持持久化（通过 StorageAdapter）
  - Task 支持类型、绑定 Agent、依赖关系
"""

import asyncio
import inspect
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from ccserver.storage.base import StorageAdapter


class Task:
    """单条任务记录。"""

    VALID_STATUSES = ("pending", "in_progress", "completed", "failed", "deleted")

    def __init__(
        self,
        task_id: str,
        subject: str,
        description: str,
        task_type: str = "general",
        agent_id: str | None = None,
        agent_type: str | None = None,
        blocked_by: list[str] | None = None,
        blocks: list[str] | None = None,
    ):
        self.id = task_id
        self.subject = subject
        self.description = description
        self.status = "pending"
        self.task_type = task_type
        self.agent_id = agent_id
        self.agent_type = agent_type
        self.blocked_by = blocked_by or []
        self.blocks = blocks or []
        self.started_at: datetime | None = None
        self.completed_at: datetime | None = None
        self.output_summary: str | None = None
        self.output_data: dict | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "status": self.status,
            "task_type": self.task_type,
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "blocked_by": self.blocked_by,
            "blocks": self.blocks,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "output_summary": self.output_summary,
            "output_data": self.output_data,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        """从字典反序列化 Task 对象。"""
        task = cls(
            task_id=str(data["id"]),
            subject=data["subject"],
            description=data.get("description", ""),
            task_type=data.get("task_type", "general"),
            agent_id=data.get("agent_id"),
            agent_type=data.get("agent_type"),
            blocked_by=data.get("blocked_by") or [],
            blocks=data.get("blocks") or [],
        )
        task.status = data.get("status", "pending")
        if data.get("started_at"):
            task.started_at = datetime.fromisoformat(data["started_at"])
        if data.get("completed_at"):
            task.completed_at = datetime.fromisoformat(data["completed_at"])
        task.output_summary = data.get("output_summary")
        task.output_data = data.get("output_data")
        return task

    def render_line(self) -> str:
        marker = {
            "pending": "[ ]",
            "in_progress": "[>]",
            "completed": "[x]",
            "failed": "[!]",
            "deleted": "[d]",
        }
        agent_tag = f" @{self.agent_id}" if self.agent_id else ""
        return f"{marker[self.status]} #{self.id}: {self.subject}{agent_tag}"


class TaskManager:
    """
    支持持久化的任务列表管理器，绑定到当前 Session 生命周期。
    通过 StorageAdapter 将任务保存到文件或数据库，重启后可恢复。
    """

    def __init__(self, session_id: str, adapter: StorageAdapter | None = None):
        self.session_id = session_id
        self._adapter = adapter
        self._tasks: dict[str, Task] = {}
        self._counter = 0

        if self._adapter is not None:
            self._load_from_storage()

    @staticmethod
    def _maybe_await(coro_or_result: Any) -> Any:
        """
        兼容同步与异步 adapter：如果返回的是协程，则运行事件循环直到完成。
        注意：仅在非异步上下文中调用；若已在异步上下文中，应由调用方 await。
        """
        if inspect.isawaitable(coro_or_result):
            try:
                loop = asyncio.get_running_loop()
                # 若已有运行中的事件循环，使用 run_coroutine_threadpool 避免嵌套错误
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, coro_or_result)
                    return future.result()
            except RuntimeError:
                # 无运行中的事件循环，可直接 asyncio.run
                return asyncio.run(coro_or_result)
        return coro_or_result

    def _load_from_storage(self) -> None:
        """启动时从 StorageAdapter 加载所有任务与计数器。"""
        assert self._adapter is not None

        tasks_data = self._maybe_await(self._adapter.list_tasks(self.session_id))
        for data in tasks_data:
            task = Task.from_dict(data)
            self._tasks[task.id] = task

        self._counter = self._maybe_await(
            self._adapter.get_task_counter(self.session_id)
        )
        logger.debug(
            "TaskManager loaded | session={} tasks={} counter={}",
            self.session_id[:8], len(self._tasks), self._counter
        )

    def _persist_task(self, task: Task) -> None:
        """将单个任务持久化到 adapter。"""
        if self._adapter is None:
            return
        self._maybe_await(self._adapter.update_task(self.session_id, task.to_dict()))

    def _persist_counter(self) -> None:
        """持久化自增计数器。"""
        if self._adapter is None:
            return
        self._maybe_await(
            self._adapter.set_task_counter(self.session_id, self._counter)
        )

    def _next_id(self) -> str:
        self._counter += 1
        self._persist_counter()
        return str(self._counter)

    def create(
        self,
        subject: str,
        description: str,
        task_type: str = "general",
        agent_id: str | None = None,
        agent_type: str | None = None,
        blocked_by: list[str] | None = None,
        blocks: list[str] | None = None,
    ) -> Task:
        """创建新任务，返回创建后的 Task 对象。"""
        if not subject.strip():
            raise ValueError("subject is required")
        task_id = self._next_id()
        task = Task(
            task_id=task_id,
            subject=subject.strip(),
            description=description.strip(),
            task_type=task_type,
            agent_id=agent_id,
            agent_type=agent_type,
            blocked_by=blocked_by or [],
            blocks=blocks or [],
        )
        self._tasks[task_id] = task
        # 自动维护：向所有前驱的 blocks 中注册此任务
        self._sync_blocks_for_blocked_by(task)
        self._persist_task(task)
        logger.debug("TaskManager: created | id={} subject={}", task_id, task.subject)
        return task

    def get(self, task_id: str) -> Task:
        """按 ID 获取任务，不存在则抛 ValueError。"""
        task = self._tasks.get(str(task_id))
        if task is None:
            raise ValueError(f"Task #{task_id} not found")
        return task

    def update(
        self,
        task_id: str,
        status: str | None = None,
        subject: str | None = None,
        description: str | None = None,
        task_type: str | None = None,
        agent_id: str | None = None,
        agent_type: str | None = None,
        blocked_by: list[str] | None = None,
        blocks: list[str] | None = None,
    ) -> Task:
        """
        更新任务的一个或多个字段，返回更新后的 Task 对象。
        只传入需要修改的字段，其余保持不变。
        """
        task = self.get(task_id)

        if status is not None:
            if status not in Task.VALID_STATUSES:
                raise ValueError(f"Invalid status '{status}'. Valid values: {Task.VALID_STATUSES}")
            task.status = status

        if subject is not None:
            if not subject.strip():
                raise ValueError("subject cannot be empty")
            task.subject = subject.strip()

        if description is not None:
            task.description = description.strip()

        if task_type is not None:
            task.task_type = task_type

        if agent_id is not None:
            task.agent_id = agent_id

        if agent_type is not None:
            task.agent_type = agent_type

        if blocked_by is not None:
            task.blocked_by = blocked_by
            self._sync_blocks_for_blocked_by(task)

        if blocks is not None:
            task.blocks = blocks
            self._sync_blocked_by_for_blocks(task)

        self._persist_task(task)
        logger.debug("TaskManager: updated | id={}", task_id)
        return task

    def bind_agent(self, task_id: str, agent_id: str, agent_type: str | None = None) -> Task:
        """将任务绑定到指定 Agent，并将状态设为 in_progress。"""
        task = self.get(task_id)
        task.agent_id = agent_id
        if agent_type is not None:
            task.agent_type = agent_type
        task.status = "in_progress"
        task.started_at = datetime.now(timezone.utc)
        self._persist_task(task)
        logger.debug("TaskManager: bind_agent | id={} agent={}", task_id, agent_id)
        return task

    def complete(
        self,
        task_id: str,
        summary: str,
        output_data: dict | None = None,
    ) -> Task:
        """标记任务完成，记录完成时间和输出结果。"""
        task = self.get(task_id)
        task.status = "completed"
        task.completed_at = datetime.now(timezone.utc)
        task.output_summary = summary
        if output_data is not None:
            task.output_data = output_data
        self._persist_task(task)
        # 自动维护：检查被此任务阻塞的后继，将其从自身 blocks 中移除（已完成不再阻塞）
        # 同时持久化所有被解锁的后继
        for blocked_id in list(task.blocks):
            blocked = self._tasks.get(str(blocked_id))
            if blocked is not None:
                if task_id in blocked.blocked_by:
                    blocked.blocked_by.remove(task_id)
                self._persist_task(blocked)
        task.blocks = []   # 完成后的任务不再阻塞任何人
        self._persist_task(task)
        logger.debug("TaskManager: complete | id={}", task_id)
        return task

    def fail(self, task_id: str, reason: str) -> Task:
        """标记任务失败，记录失败原因。"""
        task = self.get(task_id)
        task.status = "failed"
        task.completed_at = datetime.now(timezone.utc)
        task.output_summary = reason
        # 与 complete 一样，维护后继的 blocked_by 关系
        for blocked_id in list(task.blocks):
            blocked = self._tasks.get(str(blocked_id))
            if blocked is not None:
                if task_id in blocked.blocked_by:
                    blocked.blocked_by.remove(task_id)
                self._persist_task(blocked)
        task.blocks = []
        self._persist_task(task)
        logger.debug("TaskManager: fail | id={} reason={}", task_id, reason)
        return task

    def can_start(self, task: Task) -> bool:
        """检查任务是否可以开始（所有 blocked_by 依赖都已完成）。"""
        for dep_id in task.blocked_by:
            dep = self._tasks.get(str(dep_id))
            if dep is None:
                # 若内存中没有，尝试从存储加载（使用 adapter 时）
                if self._adapter is not None:
                    dep_data = self._maybe_await(
                        self._adapter.load_task(self.session_id, str(dep_id))
                    )
                    if dep_data is None:
                        return False
                    dep = Task.from_dict(dep_data)
                else:
                    return False
            if dep.status != "completed":
                return False
        return True

    # ── 双向依赖自动维护 ─────────────────────────────────────────────────────

    def _sync_blocks_for_blocked_by(self, task: Task) -> None:
        """
        当 task.blocked_by 发生变化时，调用此方法自动维护反向关系：
        1. 被移除的前驱：从其 blocks 中去掉 task.id
        2. 新增的前驱：向其 blocks 中加入 task.id
        注意：只更新内存中的对象，由调用方负责持久化。
        """
        new_blocked_by = set(task.blocked_by)
        # 从存储加载所有相关任务到内存（确保 _tasks 中有完整数据）
        for dep_id in new_blocked_by:
            if str(dep_id) not in self._tasks and self._adapter is not None:
                dep_data = self._maybe_await(
                    self._adapter.load_task(self.session_id, str(dep_id))
                )
                if dep_data is not None:
                    self._tasks[str(dep_id)] = Task.from_dict(dep_data)
        # 向所有前驱的 blocks 中注册此任务（自动追加，不重复）
        for dep_id in new_blocked_by:
            if str(dep_id) == task.id:
                continue
            dep = self._tasks.get(str(dep_id))
            if dep is not None and task.id not in dep.blocks:
                dep.blocks.append(task.id)

    def _sync_blocked_by_for_blocks(self, task: Task) -> None:
        """
        当 task.blocks 发生变化时，调用此方法自动维护反向关系：
        向所有后继的 blocked_by 中注册此任务（自动追加，不重复）。
        """
        for blocked_id in task.blocks:
            if str(blocked_id) == task.id:
                continue
            blocked = self._tasks.get(str(blocked_id))
            if blocked is not None and task.id not in blocked.blocked_by:
                blocked.blocked_by.append(task.id)

    def list_all(self) -> list[Task]:
        """返回所有未删除的任务（按创建顺序）。"""
        return [t for t in self._tasks.values() if t.status != "deleted"]

    def render_list(self) -> str:
        """生成给 LLM 看的任务列表字符串。"""
        tasks = self.list_all()
        if not tasks:
            return "No tasks."
        lines = [t.render_line() for t in tasks]
        done = sum(1 for t in tasks if t.status == "completed")
        lines.append(f"\n({done}/{len(tasks)} completed)")
        return "\n".join(lines)
