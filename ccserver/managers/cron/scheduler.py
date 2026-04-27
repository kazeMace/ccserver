"""
managers/cron/scheduler.py — TaskScheduler 定时任务调度引擎。

设计原则：
- Session 级调度器（非全局），生命周期与 Session 绑定
- 支持四种触发类型：cron / interval / countdown / once
- 支持生命周期控制：enabled / max_triggers / end_time
- 仿 TeamMailboxPoller 的标准 asyncio 协程模式：start() / stop() / is_alive
- 触发时将 prompt 注入 root_agent.context.inbox，由 _drain_inbox_and_respond() 消费
- durable=True 的任务写入磁盘，Session 重启后自动恢复调度

向后兼容：CronScheduler 是 TaskScheduler 的别名。

状态机（ScheduledTask）：
    scheduled ───到期触发──→ triggered ──检查生命周期──→ scheduled（循环）
                                                  └───once/countdown──→ deleted
                                                  └───超次数/超期─────→ expired
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal, Optional

from loguru import logger

from ccserver.utils.async_compat import maybe_await
from .models import ScheduledTask
from .cron_parser import parse_cron_next_run, compute_jitter_delay

if TYPE_CHECKING:
    from ccserver.session import Session


# ─── TaskScheduler ─────────────────────────────────────────────────────────────


class TaskScheduler:
    """
    Session 级定时任务调度器，支持 cron / interval / countdown / once 四种触发类型。

    标准协程模式：
    - start() / stop() / is_alive 属性
    - 内部运行每秒 tick 的 _run() 协程
    - 到期任务通过 jitter 延迟后注入 inbox
    - 支持生命周期控制：enabled / max_triggers / end_time
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

        # 内存中的任务表：task_id -> ScheduledTask
        self._tasks: dict[str, ScheduledTask] = {}

        # 待注入 inbox 的待触发任务（root_agent 尚未创建时暂存）
        self._pending_triggers: list[ScheduledTask] = []

        # 后台协程 task 引用
        self._task: asyncio.Task | None = None

        # 统计
        self._trigger_count: int = 0
        self._error_count: int = 0

        logger.debug("TaskScheduler initialized | session_id={}", self._session_id[:8])

    # ── 生命周期 ────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """启动后台调度协程。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            logger.info(
                "TaskScheduler started | session_id={} tasks={}",
                self._session_id[:8], len(self._tasks),
            )

    def stop(self) -> None:
        """停止调度协程。"""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("TaskScheduler stopped | session_id={}", self._session_id[:8])

    @property
    def is_alive(self) -> bool:
        """返回调度协程是否仍在运行。"""
        return self._task is not None and not self._task.done()

    # ── 公开 API ────────────────────────────────────────────────────────────────

    def create(
        self,
        prompt: str,
        trigger_type: Literal["cron", "interval", "countdown", "once"] = "interval",
        cron_expr: str = "",
        interval_seconds: int = 0,
        run_at: datetime | None = None,
        jitter_max: int = 0,
        durable: bool = False,
        enabled: bool = True,
        max_triggers: int | None = None,
        end_time: datetime | None = None,
    ) -> ScheduledTask:
        """
        创建定时任务。

        Args:
            prompt:           触发时注入 inbox 的 prompt 文本。
            trigger_type:     触发类型：cron / interval / countdown / once。
            cron_expr:        5 字段 cron 表达式，trigger_type=cron 时必填。
            interval_seconds: 间隔秒数，trigger_type=interval/countdown 时必填。
            run_at:           绝对触发时间（UTC），trigger_type=once 时必填。
            jitter_max:       最大随机延迟秒数，默认 0。
            durable:          True=写磁盘，Session 重启后能恢复。
            enabled:          是否启用，默认 True。
            max_triggers:     最大触发次数，None 表示无限。
            end_time:         截止时间（UTC），None 表示永不过期。

        Returns:
            新建的 ScheduledTask 实例。

        Raises:
            ValueError: 参数组合无效。
        """
        # ── 参数校验 ──
        if trigger_type == "cron":
            if not cron_expr:
                raise ValueError("cron_expr is required for trigger_type=cron")
        elif trigger_type in ("interval", "countdown"):
            if interval_seconds <= 0:
                raise ValueError(
                    f"interval_seconds must be positive for trigger_type={trigger_type}, "
                    f"got {interval_seconds}"
                )
        elif trigger_type == "once":
            if run_at is None:
                raise ValueError("run_at is required for trigger_type=once")

        # ── 计算首次 next_run_at ──
        now = datetime.now(timezone.utc)
        next_run_at = self._compute_next_run(
            trigger_type=trigger_type,
            cron_expr=cron_expr,
            interval_seconds=interval_seconds,
            run_at=run_at,
            base_time=now,
            last_triggered_at=None,
        )

        task = ScheduledTask(
            prompt=prompt,
            trigger_type=trigger_type,
            cron_expr=cron_expr,
            interval_seconds=interval_seconds,
            run_at=run_at,
            enabled=enabled,
            max_triggers=max_triggers,
            end_time=end_time,
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
            "ScheduledTask created | task_id={} type={} next_run={} "
            "durable={} enabled={} max={} end={}",
            task.task_id, task.trigger_type, task.next_run_at.isoformat() if task.next_run_at else None,
            durable, enabled, max_triggers,
            end_time.isoformat() if end_time else None,
        )
        return task

    def update(
        self,
        task_id: str,
        prompt: str | None = None,
        enabled: bool | None = None,
        max_triggers: int | None = None,
        end_time: datetime | None = None,
        cron_expr: str | None = None,
        interval_seconds: int | None = None,
    ) -> ScheduledTask:
        """
        更新现有任务的配置。

        Args:
            task_id:          任务 ID。
            prompt:           新 prompt，None 表示不修改。
            enabled:          新启用状态，None 表示不修改。
            max_triggers:     新最大触发次数，None 表示不修改。
            end_time:         新截止时间，None 表示不修改。
            cron_expr:        新 cron 表达式，None 表示不修改。
            interval_seconds: 新间隔秒数，None 表示不修改。

        Returns:
            更新后的 ScheduledTask。

        Raises:
            ValueError: 任务不存在。
        """
        task = self._tasks.get(task_id)
        if task is None:
            raise ValueError(f"Task '{task_id}' not found")

        # 更新字段
        if prompt is not None:
            task.prompt = prompt
        if enabled is not None:
            task.set_enabled(enabled)
        if max_triggers is not None:
            task.max_triggers = max_triggers
        if end_time is not None:
            task.end_time = end_time
        if cron_expr is not None and task.is_cron:
            task.cron_expr = cron_expr
            # 重新计算 next_run_at
            task.next_run_at = parse_cron_next_run(cron_expr, datetime.now(timezone.utc))
        if interval_seconds is not None and task.trigger_type in ("interval", "countdown"):
            task.interval_seconds = interval_seconds
            # 重新计算 next_run_at
            if task.is_interval:
                base = task.last_triggered_at or task.created_at
                task.next_run_at = base + timedelta(seconds=interval_seconds)
            elif task.is_countdown:
                task.next_run_at = task.created_at + timedelta(seconds=interval_seconds)

        # 如果之前 expired，恢复为 scheduled
        if task.status == "expired":
            task.status = "scheduled"

        if task.durable:
            self._save_task(task)

        logger.info("ScheduledTask updated | task_id={}", task_id)
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
            logger.debug("ScheduledTask.delete not found | task_id={}", task_id)
            return False

        task.mark_deleted()
        del self._tasks[task_id]

        if task.durable:
            self._delete_task(task_id)

        logger.info("ScheduledTask deleted | task_id={}", task_id)
        return True

    def toggle(self, task_id: str) -> tuple[bool, bool]:
        """
        启用/禁用切换。

        Args:
            task_id: 任务 ID。

        Returns:
            (found, new_enabled) 元组。
        """
        task = self._tasks.get(task_id)
        if task is None:
            return False, False

        new_enabled = not task.enabled
        task.set_enabled(new_enabled)

        if task.durable:
            self._save_task(task)

        return True, new_enabled

    def get(self, task_id: str) -> ScheduledTask | None:
        """按 ID 获取任务。"""
        return self._tasks.get(task_id)

    def list_all(self) -> list[ScheduledTask]:
        """
        返回当前所有任务（含已过期的）。

        Returns:
            按 task_id 排序的 ScheduledTask 列表。
        """
        return sorted(self._tasks.values(), key=lambda t: t.task_id)

    def list_active(self) -> list[ScheduledTask]:
        """
        返回所有活跃任务（非 deleted/expired）。

        Returns:
            活跃任务列表。
        """
        return [t for t in self._tasks.values() if not t.is_done]

    def load_durable_tasks(self) -> None:
        """
        从磁盘恢复所有 durable=True 的任务。

        由 Session 初始化时调用（参见 Session.__post_init__）。
        """
        if self._session is None or self._session.storage is None:
            return

        raw_tasks: list[dict] = []
        try:
            raw_tasks = maybe_await(
                self._session.storage.list_cron_tasks(self._session_id)
            )
        except (OSError, ValueError) as e:
            logger.warning(
                "TaskScheduler.load_durable_tasks failed | session_id={} error={}",
                self._session_id[:8], e,
            )
            return

        for raw in raw_tasks:
            try:
                task = ScheduledTask.from_dict(raw)
            except (ValueError, KeyError, TypeError) as e:
                logger.warning(
                    "ScheduledTask.restore skipped (corrupted data) | raw={} error={}",
                    raw, e,
                )
                continue

            # 跳过已删除/已过期的
            if task.is_done:
                continue

            self._tasks[task.task_id] = task
            logger.debug(
                "ScheduledTask restored | task_id={} type={} next_run={}",
                task.task_id, task.trigger_type, task.next_run_at,
            )

        logger.info(
            "TaskScheduler loaded durable tasks | session_id={} count={}",
            self._session_id[:8], len(self._tasks),
        )

    # ── 内部调度逻辑 ────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        """
        核心调度循环：每秒检查一次是否有到期任务。
        """
        try:
            while True:
                now = datetime.now(timezone.utc)

                # 检查待注入的暂存任务（root_agent 尚未创建时暂存）
                try:
                    await self._drain_pending_triggers()
                except Exception as e:
                    self._error_count += 1
                    logger.exception(
                        "TaskScheduler drain_pending_triggers error | session_id={} error={}",
                        self._session_id[:8], e,
                    )

                # 检查到期任务
                due_tasks = [
                    t for t in self._tasks.values()
                    if t.status == "scheduled"
                    and t.next_run_at is not None
                    and t.next_run_at <= now
                ]

                for task in due_tasks:
                    try:
                        # 生命周期检查
                        can_trigger, reason = task.check_lifecycle(now)
                        if not can_trigger:
                            if reason in ("end_time_reached", "max_triggers_reached"):
                                # 自动删除过期任务
                                self._tasks.pop(task.task_id, None)
                                if task.durable:
                                    self._delete_task(task.task_id)
                                logger.info(
                                    "ScheduledTask auto-removed | task_id={} reason={}",
                                    task.task_id, reason,
                                )
                            else:
                                logger.debug(
                                    "ScheduledTask skipped | task_id={} reason={}",
                                    task.task_id, reason,
                                )
                            continue

                        await self._schedule_trigger(task)
                    except Exception as e:
                        self._error_count += 1
                        logger.exception(
                            "TaskScheduler _schedule_trigger error | task_id={} error={}",
                            task.task_id, e,
                        )

                await asyncio.sleep(self.CHECK_INTERVAL)

        except asyncio.CancelledError:
            logger.debug("TaskScheduler cancelled | session_id={}", self._session_id[:8])
            raise
        except Exception as e:
            self._error_count += 1
            logger.exception(
                "TaskScheduler fatal error | session_id={} error={}",
                self._session_id[:8], e,
            )
            raise

    async def _schedule_trigger(self, task: ScheduledTask) -> None:
        """
        为到期任务安排触发（应用 jitter 后注入 inbox）。

        Args:
            task: 已到期的 ScheduledTask。
        """
        # 应用确定性 jitter
        if task.jitter_max > 0:
            delay = compute_jitter_delay(task.jitter_max, task.jitter_seed)
        else:
            delay = 0

        triggered_at = datetime.now(timezone.utc)
        if delay > 0:
            logger.debug(
                "ScheduledTask jitter delay | task_id={} delay={}s",
                task.task_id, delay,
            )
            await asyncio.sleep(delay)
            triggered_at = datetime.now(timezone.utc)

        # 注入 inbox
        await self._inject_inbox(task, triggered_at)

        # 更新任务状态
        task.mark_triggered(triggered_at)
        self._trigger_count += 1

        if task.trigger_type in ("once", "countdown"):
            # 一次性/倒计时任务：删除
            self._tasks.pop(task.task_id, None)
            if task.durable:
                self._delete_task(task.task_id)
            logger.info(
                "ScheduledTask completed ({}) | task_id={} trigger_count={}",
                task.trigger_type, task.task_id, task.trigger_count,
            )
        else:
            # 循环任务：重新计算下次触发时间
            task.status = "scheduled"
            task.next_run_at = self._compute_next_run(
                trigger_type=task.trigger_type,
                cron_expr=task.cron_expr,
                interval_seconds=task.interval_seconds,
                run_at=task.run_at,
                base_time=triggered_at,
                last_triggered_at=triggered_at,
            )
            if task.durable:
                self._save_task(task)
            logger.info(
                "ScheduledTask rescheduled | task_id={} type={} next_run={}",
                task.task_id, task.trigger_type,
                task.next_run_at.isoformat() if task.next_run_at else None,
            )

    def _compute_next_run(
        self,
        trigger_type: str,
        cron_expr: str,
        interval_seconds: int,
        run_at: datetime | None,
        base_time: datetime,
        last_triggered_at: datetime | None,
    ) -> datetime | None:
        """
        根据触发类型计算下次触发时间。

        Args:
            trigger_type:     触发类型。
            cron_expr:        cron 表达式。
            interval_seconds: 间隔秒数。
            run_at:           固定触发时间。
            base_time:        基准时间（now）。
            last_triggered_at: 上次触发时间（interval 类型需要）。

        Returns:
            下次触发时间，或 None（countdown/once 已触发）。
        """
        if trigger_type == "cron":
            return parse_cron_next_run(cron_expr, base_time)

        if trigger_type == "interval":
            base = last_triggered_at or base_time
            return base + timedelta(seconds=interval_seconds)

        if trigger_type == "countdown":
            # countdown 只触发一次，在创建时已计算
            return base_time + timedelta(seconds=interval_seconds)

        if trigger_type == "once":
            return run_at

        return None

    async def _inject_inbox(self, task: ScheduledTask, triggered_at: datetime) -> None:
        """
        将任务 prompt 注入 root_agent 的 inbox，同时通过 EventBus 广播触发事件。

        如果 root_agent 尚未创建（Session 初始化阶段），暂存到 _pending_triggers。

        Args:
            task:         要触发的任务。
            triggered_at: 实际触发时间（含 jitter 后）。
        """
        # ── 1. 构造触发事件 payload ──
        trigger_payload = {
            "msg_type": "scheduled_task_trigger",
            "task_id": task.task_id,
            "prompt": task.prompt,
            "trigger_type": task.trigger_type,
            "triggered_at": triggered_at.isoformat(),
            "trigger_count": task.trigger_count + 1,
            "session_id": self._session_id,
        }

        # ── 2. 注入 root_agent inbox（原有行为）──
        root = self._session._root_agent
        if root is None:
            # root_agent 尚未创建，暂存
            if task not in self._pending_triggers:
                self._pending_triggers.append(task)
                logger.debug(
                    "ScheduledTask pending (no root_agent) | task_id={}",
                    task.task_id,
                )
            return

        inbox = root.context.inbox
        if inbox is not None:
            await inbox.put(trigger_payload)
        else:
            logger.warning(
                "ScheduledTask inject skipped (no inbox) | task_id={}",
                task.task_id,
            )

        # ── 3. 通过 EventBus 广播（跨平台推送：TUI/WebChat/Feishu/Discord）──
        event_bus = getattr(self._session, "event_bus", None)
        if event_bus is not None:
            try:
                from ccserver.event_bus import AgentEvent
                event = AgentEvent(
                    agent_id=root.context.agent_id if root else "scheduler",
                    session_id=self._session.id,
                    sender_type="scheduler",
                    type="scheduled_task_triggered",
                    payload=trigger_payload,
                )
                await event_bus.publish(event)
            except Exception as e:
                logger.warning(
                    "ScheduledTask EventBus publish failed | task_id={} error={}",
                    task.task_id, e,
                )

        # ── 4. 如果 agent 处于 idle/done 状态，主动触发它运行 ──
        if root is not None and root.state.phase in ("idle", "done"):
            asyncio.create_task(self._trigger_agent_run(root, task.task_id))

        logger.debug(
            "ScheduledTask injected | task_id={} prompt={!r:.50}",
            task.task_id, task.prompt[:50],
        )

    async def _trigger_agent_run(self, root, task_id: str) -> None:
        """
        当定时任务触发时，如果 root agent 处于 idle/done 状态，
        临时将 emitter 替换为 BusEmitter，调用 _loop() 处理 inbox。

        事件通过 EventBus 广播，所有在线的客户端（SSE/WebSocket）均可收到。
        执行完成后恢复原始 emitter，避免影响后续正常聊天流程。
        """
        from ccserver.emitters.bus_emitter import BusEmitter

        original_emitter = root.emitter
        bus_emitter = BusEmitter(
            bus=self._session.event_bus,
            agent_id=root.context.agent_id,
            session_id=self._session.id,
            sender_type="scheduler",
        )
        root.emitter = bus_emitter
        try:
            logger.info(
                "ScheduledTask triggering agent run | task_id={} agent_id={} phase={}",
                task_id, root.context.agent_id, root.state.phase,
            )
            await root._loop()
        except Exception as e:
            logger.error(
                "ScheduledTask agent run failed | task_id={} error={}",
                task_id, e,
            )
        finally:
            root.emitter = original_emitter
            logger.debug(
                "ScheduledTask agent run finished | task_id={} emitter restored",
                task_id,
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
                "msg_type": "scheduled_task_trigger",
                "task_id": task.task_id,
                "prompt": task.prompt,
                "trigger_type": task.trigger_type,
                "triggered_at": triggered_at.isoformat(),
            })
            task.mark_triggered(triggered_at)
            self._trigger_count += 1

            if task.trigger_type in ("once", "countdown"):
                self._tasks.pop(task.task_id, None)
                if task.durable:
                    self._delete_task(task.task_id)
            else:
                task.status = "scheduled"
                task.next_run_at = self._compute_next_run(
                    trigger_type=task.trigger_type,
                    cron_expr=task.cron_expr,
                    interval_seconds=task.interval_seconds,
                    run_at=task.run_at,
                    base_time=triggered_at,
                    last_triggered_at=triggered_at,
                )
                if task.durable:
                    self._save_task(task)

        logger.info(
            "TaskScheduler drained pending triggers | count={}",
            len(pending),
        )

    # ── 持久化 ─────────────────────────────────────────────────────────────────

    def _save_task(self, task: ScheduledTask) -> None:
        """将任务写入存储（durable=True 时调用）。兼容 sync / async adapter。"""
        try:
            maybe_await(
                self._session.storage.create_cron_task(
                    self._session_id, task.to_dict(),
                )
            )
        except Exception as e:
            logger.error(
                "ScheduledTask.save failed | task_id={} error={}",
                task.task_id, e,
            )

    def _delete_task(self, task_id: str) -> None:
        """从存储删除任务（durable=True 时调用）。兼容 sync / async adapter。"""
        try:
            maybe_await(
                self._session.storage.delete_cron_task(self._session_id, task_id)
            )
        except Exception as e:
            logger.error(
                "ScheduledTask.delete from storage failed | task_id={} error={}",
                task_id, e,
            )


# ─── 向后兼容别名 ─────────────────────────────────────────────────────────────

CronScheduler = TaskScheduler
"""CronScheduler 是 TaskScheduler 的别名，保持向后兼容。旧代码可直接使用 CronScheduler。"""
