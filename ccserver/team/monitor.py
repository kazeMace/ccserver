"""
team.monitor — TeamHealthMonitor 实现。

功能：定期扫描所有 Session 中的 Team，检查 Dispatcher、PermissionRelay、
      MailboxPoller 是否存活，自动重启死亡的组件（自愈）。
"""

import asyncio
from loguru import logger

from ccserver import agent_registry
from ccserver.team.mailbox import TeamMailbox
from ccserver.team.dispatcher import TeamTaskDispatcher
from ccserver.team.permission_relay import TeamPermissionRelay
from ccserver.team.poller import TeamMailboxPoller


class TeamHealthMonitor:
    """
    Agent Team 组件健康监控器。

    以固定间隔扫描所有已注册 Team 的关键后台协程：
      - TeamTaskDispatcher
      - TeamPermissionRelay
      - TeamMailboxPoller（附着在 BackgroundAgentHandle 上）

    发现死亡的组件时自动调用 start() 重启，并记录警告日志。
    """

    def __init__(self, session_manager, interval: float = 30.0):
        """
        初始化监控器。

        Args:
            session_manager: SessionManager 实例，用于遍历所有 Session
            interval:        扫描间隔（秒），默认 30 秒
        """
        self.session_manager = session_manager
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._loop_count: int = 0
        self._restart_count: int = 0

    def start(self) -> None:
        """启动监控协程。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            logger.info("TeamHealthMonitor started | interval={}s", self.interval)

    def stop(self) -> None:
        """停止监控协程。"""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("TeamHealthMonitor stopped")

    @property
    def is_alive(self) -> bool:
        """返回监控协程是否仍在运行。"""
        return self._task is not None and not self._task.done()

    async def _run(self) -> None:
        """核心监控循环。"""
        try:
            while True:
                self._loop_count += 1
                await self._check_all_teams()
                await self._check_all_pollers()
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            logger.debug("TeamHealthMonitor cancelled")
        except Exception as e:
            logger.error("TeamHealthMonitor fatal error | error={}", e)

    async def _check_all_teams(self) -> None:
        """遍历所有 Session 的 TeamRegistry，检查 Dispatcher 和 Relay。"""
        # _sessions 是内存中的 Session 对象字典，list_all() 返回的是 dict 元数据，不能用
        sessions = list(self.session_manager._sessions.values())
        for session in sessions:
            registry = session.team_registry
            if registry is None:
                continue

            for team in registry.list_teams():
                mailbox = getattr(team, "_mailbox", None)
                if mailbox is None:
                    mailbox = TeamMailbox(team.name, session.storage)
                    team._mailbox = mailbox

                # 检查 Dispatcher
                dispatcher = getattr(team, "_dispatcher", None)
                if dispatcher is None or not dispatcher.is_alive:
                    logger.warning(
                        "TeamHealthMonitor: dispatcher dead, restarting | team={}",
                        team.name,
                    )
                    dispatcher = TeamTaskDispatcher(team, mailbox)
                    dispatcher.start()
                    team._dispatcher = dispatcher
                    self._restart_count += 1

                # 检查 PermissionRelay
                relay = getattr(team, "_relay", None)
                if relay is None or not relay.is_alive:
                    logger.warning(
                        "TeamHealthMonitor: relay dead, restarting | team={}",
                        team.name,
                    )
                    relay = TeamPermissionRelay(team, mailbox)
                    relay.start()
                    team._relay = relay
                    self._restart_count += 1

    async def _check_all_pollers(self) -> None:
        """遍历全局 BackgroundAgentHandle，检查 TeamMailboxPoller。"""
        for handle in agent_registry.list_handles():
            poller = getattr(handle, "_team_poller", None)
            if poller is None:
                continue
            if not poller.is_alive:
                logger.warning(
                    "TeamHealthMonitor: poller dead, restarting | agent_id={}",
                    handle.agent_id,
                )
                poller.start()
                self._restart_count += 1
