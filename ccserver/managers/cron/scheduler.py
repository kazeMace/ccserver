"""
managers/cron/scheduler.py — CronScheduler 定时任务调度引擎。

设计原则：
- Session 级调度器（非全局），生命周期与 Session 绑定
- 仿 TeamMailboxPoller 的标准 asyncio 协程模式：start() / stop() / is_alive
- 触发时将 prompt 注入 root_agent.context.inbox，由 _drain_inbox_and_respond() 消费
- durable=True 的任务写入磁盘，Session 重启后自动恢复调度

状态机（CronTask）：
    scheduled ───到期触发──→ triggered
        ├── 一次性（mode=once）：立即删除
        └── 循环（mode=recurring）：重新计算 next_run_at，回归 scheduled
    scheduled ───delete──→ deleted
"""

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal, Optional

from loguru import logger

from .models import CronTask
from .cron_parser import parse_cron_next_run, compute_jitter_delay

if TYPE_CHECKING:
    from ccserver.session import Session


# ─── CronScheduler ─────────────────────────────────────────────────────────────


class CronScheduler:
    """
    Session 级定时任务调度器。

    仿 TeamMailboxPoller 的标准协程模式：
    - start() / stop() / is_alive 属性
    - 内部运行每秒 tick 的 _run() 协程
    - 到期任务通过 jitter 延迟后注入 inbox
    """

    # 每秒检查一次是否有任务到期
    CHECK_INTERVAL: float = 1.0

    def __init__(self, session: "Session") -> None:
        """
        初始化调度器。

        Args:
            session: 所属 Session 实例。
        """
        self._session = session
        self._session_id = session.id

        # 内存中的任务表：task_id -> CronTask
        self._tasks: dict[str, CronTask] = {}

        # 待注入 inbox 的待触发任务（root_agent 尚未创建时暂存）
        self._pending_triggers: list[CronTask] = []

        # 后台协程 task 引用
        self._task: asyncio.Task | None = None

        # 统计
        self._trigger_count: int = 0
        self._error_count: int = 0

        logger.debug("CronScheduler initialized | session_id={}", self._session_id[:8])

    # ── 生命周期 ────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """启动后台调度协程。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            logger.info(
                "CronScheduler started | session_id={} tasks={}",
                self._session_id[:8], len(self._tasks),
            )

    def stop(self) -> None:
        """停止调度协程。"""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("CronScheduler stopped | session_id={}", self._session_id[:8])

    @property
    def is_alive(self) -> bool:
        """返回调度协程是否仍在运行。"""
        return self._task is not None and not self._task.done()

    # ── 公开 API ────────────────────────────────────────────────────────────────

    def create(
        self,
        prompt: str,
        cron_expr: str | None = None,
        run_at: datetime | None = None,
        jitter_max: int = 0,
        durable: bool = False,
        mode: Literal["once", "recurring"] = "recurring",
    ) -> CronTask:
        """
        创建定时任务。

        Args:
            prompt:     触发时注入 inbox 的 prompt 文本。
            cron_expr:  5 字段 cron 表达式（如 "*/5 * * * *"），循环任务必填。
            run_at:     一次性任务的绝对触发时间（UTC aware datetime）。
            jitter_max: 最大随机延迟秒数（防雷鸣效应），默认 0 不启用。
            durable:    True=写磁盘，Session 重启后能恢复。
            mode:       "once"（一次性）或 "recurring"（循环），默认 recurring。

        Returns:
            新建的 CronTask 实例。

        Raises:
            ValueError: cron_expr 为空（mode=recurring）或参数组合无效。
        """
        # 校验参数组合
        if mode == "recurring":
            if not cron_expr:
                raise ValueError("cron_expr is required for recurring tasks")
        else:  # mode == "once"
            if run_at is None:
                raise ValueError("run_at is required for once tasks")
            if cron_expr:
                raise ValueError("cron_expr must be empty for once tasks (use run_at)")

        # 计算下次触发时间
        now = datetime.now(timezone.utc)
        if mode == "recurring":
            next_run_at = parse_cron_next_run(cron_expr, now)
        else:
            # 一次性任务：直接使用 run_at（转为 UTC aware）
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=timezone.utc)
            else:
                run_at = run_at.astimezone(timezone.utc)
            next_run_at = run_at

        task = CronTask(
            prompt=prompt,
            cron_expr=cron_expr or "",
            mode=mode,
            next_run_at=next_run_at,
            jitter_max=jitter_max,
            durable=durable,
            status="scheduled",
            created_at=now,
        )

        self._tasks[task.task_id] = task

        if durable:
            self._save_task(task)

        logger.info(
            "CronTask created | task_id={} mode={} cron={!r} next_run={} durable={}",
            task.task_id, task.mode, task.cron_expr, task.next_run_at.isoformat(), durable,
        )

        return task

    def delete(self, task_id: str) -> bool:
        """
        删除定时任务。

        Args:
            task_id: 要删除的任务 ID。

        Returns:
            True=任务存在并已删除，False=任务不存在。
        """
        task = self._tasks.get(task_id)
        if task is None:
            logger.debug("CronTask.delete not found | task_id={}", task_id)
            return False

        task.mark_deleted()
        del self._tasks[task_id]

        if task.durable:
            self._delete_task(task_id)

        logger.info("CronTask deleted | task_id={}", task_id)
        return True

    def list_all(self) -> list[CronTask]:
        """
        返回当前所有任务（含已过期的一次性任务）。

        Returns:
            按 task_id 排序的 CronTask 列表。
        """
        return sorted(self._tasks.values(), key=lambda t: t.task_id)

    def load_durable_tasks(self) -> None:
        """
        从磁盘恢复所有 durable=True 的任务。

        由 Session 初始化时调用（参见 Session.__post_init__）。
        """
        try:
            raw_tasks = self._session.storage.list_cron_tasks(self._session_id)
        except Exception as e:
            logger.warning(
                "CronScheduler.load_durable_tasks failed | session_id={} error={}",
                self._session_id[:8], e,
            )
            return

        for raw in raw_tasks:
            try:
                task = CronTask.from_dict(raw)
                # 跳过已删除的
                if task.status == "deleted":
                    continue
                self._tasks[task.task_id] = task
                logger.debug(
                    "CronTask restored | task_id={} mode={} next_run={}",
                    task.task_id, task.mode, task.next_run_at,
                )
            except Exception as e:
                logger.warning(
                    "CronTask.restore failed | raw={} error={}",
                    raw, e,
                )

        logger.info(
            "CronScheduler loaded durable tasks | session_id={} count={}",
            self._session_id[:8], len(self._tasks),
        )

    # ── 内部调度逻辑 ────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        """
        核心调度循环：每秒检查一次是否有到期任务。

        伪代码：
            while True:
                now = utc_now()
                for task in scheduled_tasks:
                    if now >= task.next_run_at:
                        await _schedule_trigger(task, jitter)
                drain _pending_triggers (root_agent 恢复后)
                sleep(1s)
        """
        try:
            while True:
                now = datetime.now(timezone.utc)

                # 检查待注入的暂存任务（root_agent 尚未创建时暂存）
                await self._drain_pending_triggers()

                # 检查到期任务
                due_tasks = [
                    t for t in self._tasks.values()
                    if t.status == "scheduled" and t.next_run_at <= now
                ]

                for task in due_tasks:
                    await self._schedule_trigger(task)

                await asyncio.sleep(self.CHECK_INTERVAL)

        except asyncio.CancelledError:
            logger.debug("CronScheduler cancelled | session_id={}", self._session_id[:8])
        except Exception as e:
            self._error_count += 1
            logger.error(
                "CronScheduler fatal error | session_id={} error={}",
                self._session_id[:8], e,
            )

    async def _schedule_trigger(self, task: CronTask) -> None:
        """
        为到期任务安排触发（应用 jitter 后注入 inbox）。

        Args:
            task: 已到期的 CronTask。
        """
        # 应用确定性 jitter
        if task.jitter_max > 0:
            delay = compute_jitter_delay(task.jitter_max, task.jitter_seed)
        else:
            delay = 0

        triggered_at = datetime.now(timezone.utc)
        if delay > 0:
            logger.debug(
                "CronTask jitter delay | task_id={} delay={}s",
                task.task_id, delay,
            )
            await asyncio.sleep(delay)
            # 重新读取此刻时间（sleep 后）
            triggered_at = datetime.now(timezone.utc)

        # 注入 inbox
        await self._inject_inbox(task, triggered_at)

        # 更新任务状态
        task.mark_triggered(triggered_at)
        self._trigger_count += 1

        if task.mode == "once":
            # 一次性任务：立即删除（durable 已由 storage 层管理）
            self._tasks.pop(task.task_id, None)
            if task.durable:
                self._delete_task(task.task_id)
            logger.info(
                "CronTask once completed | task_id={} trigger_count={}",
                task.task_id, task.trigger_count,
            )
        else:
            # 循环任务：重新计算下次触发时间
            task.status = "scheduled"
            task.next_run_at = parse_cron_next_run(
                task.cron_expr, datetime.now(timezone.utc),
            )
            if task.durable:
                self._save_task(task)
            logger.info(
                "CronTask recurring rescheduled | task_id={} next_run={}",
                task.task_id, task.next_run_at.isoformat(),
            )

    async def _inject_inbox(self, task: CronTask, triggered_at: datetime) -> None:
        """
        将任务 prompt 注入 root_agent 的 inbox。

        如果 root_agent 尚未创建（Session 初始化阶段），暂存到 _pending_triggers。

        Args:
            task:         要触发的任务。
            triggered_at: 实际触发时间（含 jitter 后）。
        """
        root = self._session._root_agent
        if root is None:
            # root_agent 尚未创建，暂存
            if task not in self._pending_triggers:
                self._pending_triggers.append(task)
                logger.debug(
                    "CronTask pending (no root_agent) | task_id={}",
                    task.task_id,
                )
            return

        inbox = root.context.inbox
        if inbox is None:
            logger.warning(
                "CronTask inject skipped (no inbox) | task_id={}",
                task.task_id,
            )
            return

        await inbox.put({
            "msg_type": "cron_trigger",
            "task_id": task.task_id,
            "prompt": task.prompt,
            "mode": task.mode,
            "cron_expr": task.cron_expr,
            "triggered_at": triggered_at.isoformat(),
        })

        logger.debug(
            "CronTask injected | task_id={} prompt={!r:.50}",
            task.task_id, task.prompt[:50],
        )

    async def _drain_pending_triggers(self) -> None:
        """
        当 root_agent 已就绪时，将暂存的待触发任务注入 inbox。
        """
        root = self._session._root_agent
        if root is None or not self._pending_triggers:
            return

        pending = self._pending_triggers
        self._pending_triggers = []

        triggered_at = datetime.now(timezone.utc)
        for task in pending:
            await root.context.inbox.put({
                "msg_type": "cron_trigger",
                "task_id": task.task_id,
                "prompt": task.prompt,
                "mode": task.mode,
                "cron_expr": task.cron_expr,
                "triggered_at": triggered_at.isoformat(),
            })
            task.mark_triggered(triggered_at)
            self._trigger_count += 1

            if task.mode == "once":
                self._tasks.pop(task.task_id, None)
                if task.durable:
                    self._delete_task(task.task_id)
            else:
                task.status = "scheduled"
                task.next_run_at = parse_cron_next_run(
                    task.cron_expr, triggered_at,
                )
                if task.durable:
                    self._save_task(task)

        logger.info(
            "CronScheduler drained pending triggers | count={}",
            len(pending),
        )

    # ── 持久化 ─────────────────────────────────────────────────────────────────

    def _save_task(self, task: CronTask) -> None:
        """将任务写入磁盘（durable=True 时调用）。"""
        try:
            self._session.storage.create_cron_task(
                self._session_id, task.to_dict(),
            )
        except Exception as e:
            logger.error(
                "CronTask.save failed | task_id={} error={}",
                task.task_id, e,
            )

    def _delete_task(self, task_id: str) -> None:
        """从磁盘删除任务（durable=True 时调用）。"""
        try:
            self._session.storage.delete_cron_task(self._session_id, task_id)
        except Exception as e:
            logger.error(
                "CronTask.delete from storage failed | task_id={} error={}",
                task_id, e,
            )
