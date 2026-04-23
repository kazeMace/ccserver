"""
team.permission_relay — TeamPermissionRelay 实现。

功能：将 teammate 产生的 PermissionRequestMessage 转发给 Team Lead，
      并将 Lead 的 PermissionResponseMessage 回写给原请求者。
      目前为骨架实现，后续与 BaseEmitter 完整对接实现双向桥接。
"""

import asyncio
from loguru import logger

from .mailbox import TeamMailbox
from .models import Team


class TeamPermissionRelay:
    """
    团队权限审批中继器。

    随 Team 创建启动，负责桥接 teammate 与 Lead 之间的权限请求/响应。
    """

    def __init__(
        self,
        team: Team,
        mailbox: TeamMailbox,
        interval: float = 2.0,
    ):
        """
        初始化中继器。

        Args:
            team:     Team 实例
            mailbox:  TeamMailbox 实例
            interval: 轮询间隔（秒），默认 2 秒
        """
        self.team = team
        self.mailbox = mailbox
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._loop_count: int = 0
        self._relayed_count: int = 0
        self._failed_count: int = 0

    def start(self) -> None:
        """启动中继协程。"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            logger.info(
                "TeamPermissionRelay started | team={} interval={}s",
                self.team.name, self.interval
            )

    def stop(self) -> None:
        """停止中继协程。"""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("TeamPermissionRelay stopped | team={}", self.team.name)

    @property
    def is_alive(self) -> bool:
        """返回中继协程是否仍在运行。"""
        return self._task is not None and not self._task.done()

    async def _run(self) -> None:
        """核心中继循环（当前仅做心跳监控）。"""
        try:
            while True:
                self._loop_count += 1
                # TODO: 扫描 lead 的 inbox 中的 permission_response 并转发给请求者
                # TODO: 扫描各 teammate 的 permission_request 并通过 emitter 通知 lead
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            logger.debug("TeamPermissionRelay cancelled | team={}", self.team.name)
        except Exception as e:
            self._failed_count += 1
            logger.error("TeamPermissionRelay fatal error | team={} error={}", self.team.name, e)
