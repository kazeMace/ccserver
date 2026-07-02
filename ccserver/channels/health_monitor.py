"""
channels/health_monitor — Channel 健康监控器。

职责：定期检查所有运行中 channel 的连接状态，发现静默超时时自动重启。

判断逻辑：
  - 各 Adapter 在收到任何平台消息时调用 touch_last_event() 更新时间戳
  - 每 check_interval_s 检查所有运行中 channel 的 last_event_at
  - 超过 stale_threshold_s 无事件 → 判定为潜在断线，触发重启
  - 每小时重启次数超过 max_restarts_per_hour → 跳过（防雪崩）

参考：OpenClaw 的 ChannelHealthMonitor（channel-health-monitor.ts）
"""

import asyncio
import time
from typing import Optional

from loguru import logger

from .lifecycle import ChannelLifecycle


class ChannelHealthMonitor:
    """
    Channel 连接健康监控器。

    在进程启动时由 server.py 创建并调用 start()，
    与 ChannelLifecycle 共生，进程退出时调用 stop()。

    Args:
        lifecycle:               ChannelLifecycle 实例
        check_interval_s:        检查间隔（秒），默认 5 分钟
        stale_threshold_s:       无事件判定为静默的阈值（秒），默认 25 分钟
        max_restarts_per_hour:   每小时最大重启次数，超出后跳过（防雪崩）
        startup_grace_s:         启动宽限期（秒），期间不做健康检查，默认 60s
    """

    def __init__(
        self,
        lifecycle: ChannelLifecycle,
        check_interval_s: float = 300,      # 5 分钟
        stale_threshold_s: float = 1500,    # 25 分钟无事件
        max_restarts_per_hour: int = 10,
        startup_grace_s: float = 60,        # 启动宽限期
    ):
        self._lifecycle = lifecycle
        self._check_interval = check_interval_s
        self._stale_threshold = stale_threshold_s
        self._max_restarts = max_restarts_per_hour
        self._startup_grace = startup_grace_s
        # channel_key → [restart timestamp列表]（用于速率限制）
        self._restart_records: dict[str, list[float]] = {}
        self._task: Optional[asyncio.Task] = None
        self._started_at: float = 0

    def start(self) -> None:
        """启动后台检查循环（幂等）。"""
        if self._task is None or self._task.done():
            self._started_at = time.monotonic()
            self._task = asyncio.create_task(self._check_loop())
            logger.info(
                "ChannelHealthMonitor started | interval={}s stale_threshold={}s max_restarts/h={}",
                self._check_interval, self._stale_threshold, self._max_restarts,
            )

    def stop(self) -> None:
        """停止后台检查循环。"""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("ChannelHealthMonitor stopped")

    async def _check_loop(self) -> None:
        """后台检查主循环。"""
        # 等待启动宽限期，避免 channel 刚启动就被误判为静默
        await asyncio.sleep(self._startup_grace)

        while True:
            try:
                await self._check_once()
            except Exception as e:
                logger.error("ChannelHealthMonitor check error | err={}", e)
            await asyncio.sleep(self._check_interval)

    async def _check_once(self) -> None:
        """检查所有运行中 channel，对静默超时的 channel 触发重启。"""
        now = time.monotonic()
        for info in self._lifecycle.list_running():
            channel_id = info["channel_id"]
            account_id = info["account_id"]
            last_event_at = info.get("last_event_at", now)

            # 判断是否静默超时
            silent_for = now - last_event_at
            if silent_for > self._stale_threshold:
                logger.warning(
                    "ChannelHealthMonitor: stale channel detected | "
                    "channel={} account={} silent={:.0f}s threshold={}s",
                    channel_id, account_id, silent_for, self._stale_threshold,
                )
                await self._maybe_restart(channel_id, account_id)

    async def _maybe_restart(self, channel_id: str, account_id: str) -> None:
        """
        按速率限制重启指定 channel。

        每小时最多重启 max_restarts_per_hour 次，超出则跳过并记录警告。
        """
        key = f"{channel_id}:{account_id}"
        now = time.monotonic()

        # 清理一小时前的旧记录
        records = [t for t in self._restart_records.get(key, []) if now - t < 3600]

        if len(records) >= self._max_restarts:
            logger.warning(
                "ChannelHealthMonitor: restart rate limit hit | "
                "channel={} account={} restarts_this_hour={}",
                channel_id, account_id, len(records),
            )
            return

        logger.info(
            "ChannelHealthMonitor: restarting stale channel | channel={} account={}",
            channel_id, account_id,
        )

        # 记录本次重启
        records.append(now)
        self._restart_records[key] = records

        try:
            cfg = self._lifecycle.get_account_config(channel_id, account_id)
            await self._lifecycle.stop_channel(channel_id, account_id)
            await self._lifecycle.start_channel(channel_id, account_id, cfg)
            logger.info(
                "ChannelHealthMonitor: channel restarted | channel={} account={}",
                channel_id, account_id,
            )
        except Exception as e:
            logger.error(
                "ChannelHealthMonitor: restart failed | channel={} account={} err={}",
                channel_id, account_id, e,
            )
