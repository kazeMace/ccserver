"""
team.dispatcher — TeamTaskDispatcher 实现。

功能：监控 Team 中 idle 的 teammate，并向其 mailbox 分配新任务。
      基于 Ready Rule 自动调度：status == pending AND blocked_by 全部完成。
"""

import asyncio
from loguru import logger

from .mailbox import TeamMailbox
from .models import Team, TeamMemberState
from .protocol import NewTaskMessage


class TeamTaskDispatcher:
    """
    团队任务调度器。

    随 Team 创建启动，负责扫描待分配任务并将其投递给空闲 teammate。
    调度规则（Ready Rule）：
        task.status == "pending" AND task_manager.can_start(task) == True

    EventBus 重构后：
      - 订阅 EventBus 的 idle 事件，收到后立即触发调度（事件驱动）
      - 保留 30 秒兜底扫描，防止事件丢失时死锁
      - 删除原有 5 秒定时盲扫主循环
    """

    # 兜底扫描间隔（秒）
    FALLBACK_INTERVAL = 30.0

    def __init__(
        self,
        team: Team,
        mailbox: TeamMailbox,
        task_manager=None,
        event_bus=None,
    ):
        """
        初始化调度器。

        Args:
            team:         Team 实例
            mailbox:      TeamMailbox 实例（用于向 teammate 发任务消息）
            task_manager: 可选的任务管理器，实现 ready 任务扫描与绑定
            event_bus:    Session 级 EventBus 实例，用于订阅 idle 事件
        """
        self.team = team
        self.mailbox = mailbox
        self.task_manager = task_manager
        self._event_bus = event_bus
        self._task: asyncio.Task | None = None
        self._loop_count: int = 0
        self._assigned_count: int = 0
        self._failed_count: int = 0
        self._last_assigned_at: float | None = None

    def start(self) -> None:
        """启动调度协程。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            logger.info(
                "TeamTaskDispatcher started | team={} fallback_interval={}s",
                self.team.name, self.FALLBACK_INTERVAL,
            )

    def stop(self) -> None:
        """停止调度协程。"""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("TeamTaskDispatcher stopped | team={}", self.team.name)

    @property
    def is_alive(self) -> bool:
        """返回调度协程是否仍在运行。"""
        return self._task is not None and not self._task.done()

    async def _run(self) -> None:
        """核心调度循环：事件驱动 + 兜底扫描。"""
        try:
            if self._event_bus is not None:
                # 同时启动 EventBus 监听和兜底扫描
                await asyncio.gather(
                    self._event_bus_loop(),
                    self._fallback_loop(),
                    return_exceptions=True,
                )
            else:
                # 无 EventBus，走纯兜底扫描
                await self._fallback_loop()
        except asyncio.CancelledError:
            logger.debug("TeamTaskDispatcher cancelled | team={}", self.team.name)
        except Exception as e:
            self._failed_count += 1
            logger.error("TeamTaskDispatcher fatal error | team={} error={}", self.team.name, e)

    async def _event_bus_loop(self) -> None:
        """订阅 EventBus idle 事件，收到后立即触发调度。"""
        from ccserver.event_bus import EventType

        def filter_fn(e):
            return e.type == EventType.IDLE
        sub_id = f"dispatcher_{self.team.name}"
        async with self._event_bus.subscribe(sub_id, filter_fn=filter_fn) as sub:
            while True:
                event = await sub.get(timeout=self.FALLBACK_INTERVAL)
                if event is not None:
                    logger.debug(
                        "Dispatcher triggered by idle event | team={} agent_id={}",
                        self.team.name, event.agent_id,
                    )
                if self.task_manager is not None:
                    await self._dispatch_once()

    async def _fallback_loop(self) -> None:
        """30 秒兜底扫描：防止事件丢失时死锁。"""
        while True:
            await asyncio.sleep(self.FALLBACK_INTERVAL)
            if self.task_manager is not None:
                await self._dispatch_once()

    async def _dispatch_once(self) -> None:
        """
        单次调度尝试：
          1. 找出所有 idle 的 teammate
          2. 找出所有 ready 的任务（pending + can_start）
          3. 按 FIFO 将任务分配给 teammate，发送 NewTaskMessage
          4. 通过 task_manager.bind_agent() 将任务设为 in_progress
        """
        assert self.task_manager is not None

        idle_members = [
            m for m in self.team.members.values()
            if m.state == TeamMemberState.IDLE and m.role.value == "teammate"
        ]
        if not idle_members:
            return

        ready_tasks = [
            t for t in self.task_manager.list_all()
            if t.status == "pending" and self.task_manager.can_start(t)
        ]
        if not ready_tasks:
            return

        # 按创建顺序排序（ID 是自增数字字符串，直接按 int 排序更稳）
        ready_tasks.sort(key=lambda t: int(t.id))

        logger.debug(
            "TeamTaskDispatcher dispatch | team={} idle={} ready={}",
            self.team.name, len(idle_members), len(ready_tasks)
        )

        for member in idle_members:
            if not ready_tasks:
                break
            task = ready_tasks.pop(0)

            msg = NewTaskMessage(
                from_agent="dispatcher",
                to_agent=member.agent_id,
                task_id=task.id,
                task_prompt=task.description or task.subject,
                text=f"[Dispatcher] 分配任务 #{task.id}: {task.subject}",
            )
            await self.mailbox.send(msg)
            self.task_manager.bind_agent(task.id, member.agent_id)

            self._assigned_count += 1
            self._last_assigned_at = asyncio.get_event_loop().time()
            logger.info(
                "TeamTaskDispatcher assigned | team={} task_id={} agent_id={} prompt={!r:.60}",
                self.team.name, task.id, member.agent_id, msg.task_prompt
            )
