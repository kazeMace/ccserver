"""
channels/lifecycle — Channel 生命周期管理。

职责：Channel 的启动、停止、状态查询、自动发现与批量启动。
从 ChannelGateway 拆出，单一职责：只管 channel 连接状态。
"""

import asyncio
import time

from loguru import logger

from .base import ChannelAccountSnapshot
from .registry import ChannelRegistry
from .config import ChannelConfig


class ChannelLifecycle:
    """
    Channel 适配器的生命周期管理器。

    管理所有已启动的 channel 账户，提供启动、停止、查询接口。
    对 channel 的"最后活跃时间戳"做记录，供 ChannelHealthMonitor 使用。

    Attributes:
        registry:   ChannelRegistry 实例
        config:     ChannelConfig 实例
        _running:   channel_id -> account_id -> {adapter, config, snapshot, last_event_at}
        _inbound_handler: 入站消息回调（由外部注入，通常是 InboundRouter.dispatch）
    """

    def __init__(
        self,
        registry: ChannelRegistry,
        config: ChannelConfig | None = None,
    ):
        self.registry = registry
        self.config = config or ChannelConfig()
        # channel_id -> account_id -> {adapter, config, snapshot, last_event_at}
        self._running: dict[str, dict[str, dict]] = {}
        # 入站消息回调（由 ChannelGateway 注入 InboundRouter.dispatch）
        self._inbound_handler = None

    def set_inbound_handler(self, handler) -> None:
        """注入入站消息回调。ChannelGateway 初始化时调用。"""
        self._inbound_handler = handler

    # ─── 生命周期 ────────────────────────────────────────────────────────────────

    async def start_channel(
        self,
        channel_id: str,
        account_id: str,
        config: dict,
    ) -> ChannelAccountSnapshot:
        """
        启动一个 channel 账户。

        Args:
            channel_id: channel ID 或别名，如 "discord"
            account_id: 账户标识
            config:     平台特定配置（token、app_id 等）

        Returns:
            启动后的账户状态快照

        Raises:
            ValueError: channel_id 未知
        """
        adapter = self.registry.get_adapter(channel_id)
        if adapter is None:
            known = list(self.registry._adapters.keys())
            raise ValueError(f"Unknown channel '{channel_id}'. Known: {known}")

        canonical = self.registry.normalize_channel_id(channel_id)
        assert canonical is not None

        # 注入入站消息回调
        if self._inbound_handler is not None:
            adapter.set_inbound_handler(self._inbound_handler)

        logger.info("Starting channel | channel={} account={}", canonical, account_id)
        snapshot = await adapter.start(account_id, config)

        self._running.setdefault(canonical, {})[account_id] = {
            "adapter": adapter,
            "config": config,
            "snapshot": snapshot,
            "last_event_at": time.monotonic(),
        }
        logger.info(
            "Channel started | channel={} account={} running={} connected={}",
            canonical, account_id, snapshot.running, snapshot.connected,
        )
        return snapshot

    async def stop_channel(self, channel_id: str, account_id: str) -> None:
        """停止一个 channel 账户。"""
        adapter = self.registry.get_adapter(channel_id)
        if adapter is None:
            logger.warning("stop_channel: unknown channel '{}'", channel_id)
            return

        canonical = self.registry.normalize_channel_id(channel_id)
        assert canonical is not None

        logger.info("Stopping channel | channel={} account={}", canonical, account_id)
        try:
            await adapter.stop(account_id)
        except Exception as e:
            logger.error(
                "Channel stop failed | channel={} account={} err={}",
                canonical, account_id, e,
            )

        if canonical in self._running:
            self._running[canonical].pop(account_id, None)
            if not self._running[canonical]:
                del self._running[canonical]

        logger.info("Channel stopped | channel={} account={}", canonical, account_id)

    async def get_status(self, channel_id: str, account_id: str) -> ChannelAccountSnapshot:
        """查询 channel 账户状态。"""
        adapter = self.registry.get_adapter(channel_id)
        if adapter is None:
            raise ValueError(f"Unknown channel '{channel_id}'")
        return await adapter.get_status(account_id)

    def list_running(self) -> list[dict]:
        """列出所有正在运行的 channel 账户。"""
        result = []
        for channel_id, accounts in self._running.items():
            for account_id, info in accounts.items():
                snapshot = info.get("snapshot")
                adapter = info.get("adapter")
                if snapshot:
                    result.append({
                        "channel_id": channel_id,
                        "account_id": account_id,
                        # 优先用 adapter 自身记录的时间戳（更准确）
                        "last_event_at": getattr(adapter, "_last_event_at", info.get("last_event_at", 0)),
                        "status": snapshot.to_dict(),
                    })
        return result

    def get_account_config(self, channel_id: str, account_id: str) -> dict:
        """获取已启动账户的配置（供 ChannelHealthMonitor 重启时使用）。"""
        canonical = self.registry.normalize_channel_id(channel_id)
        if canonical is None:
            return {}
        return self._running.get(canonical, {}).get(account_id, {}).get("config", {})

    def touch_last_event(self, channel_id: str, account_id: str) -> None:
        """
        更新 channel 最后活跃时间戳。
        各 Adapter 收到任何平台消息时调用，供 ChannelHealthMonitor 判断是否断线。
        """
        canonical = self.registry.normalize_channel_id(channel_id)
        if canonical and canonical in self._running:
            acc = self._running[canonical].get(account_id)
            if acc:
                acc["last_event_at"] = time.monotonic()

    # ─── 自动发现 + 批量启动 ────────────────────────────────────────────────────

    async def auto_discover_and_start(self) -> dict:
        """
        自动扫描适配器，并启动配置中 enabled + auto_start 的 channel。

        启动时的主入口，按顺序：
          1. 加载 channels.json 配置
          2. 扫描 adapters 目录自动注册适配器
          3. 启动配置中标记为 auto_start 的 channel

        Returns:
            {"discovered": int, "started": int, "failed": int, "skipped": int}
        """
        self.config.load()
        discovered = self.registry.discover()

        started = failed = skipped = 0
        for channel_id, account_id, account_cfg in self.config.list_auto_start():
            if not self.registry.is_registered(channel_id):
                logger.warning(
                    "Auto-start skipped: adapter not registered | channel={}",
                    channel_id,
                )
                skipped += 1
                continue

            logger.info("Auto-starting channel | channel={} account={}", channel_id, account_id)
            try:
                await self.start_channel(channel_id, account_id, account_cfg)
                started += 1
            except Exception as e:
                logger.error(
                    "Auto-start failed | channel={} account={} err={}",
                    channel_id, account_id, e,
                )
                failed += 1

        result = {"discovered": discovered, "started": started, "failed": failed, "skipped": skipped}
        logger.info(
            "Channel auto-start complete | discovered={} started={} failed={} skipped={}",
            discovered, started, failed, skipped,
        )
        return result

    async def shutdown(self) -> None:
        """停止所有正在运行的 channel。"""
        logger.info("ChannelLifecycle shutting down...")
        for channel_id, accounts in list(self._running.items()):
            for account_id in list(accounts.keys()):
                try:
                    await self.stop_channel(channel_id, account_id)
                except Exception as e:
                    logger.error(
                        "Shutdown error | channel={} account={} err={}",
                        channel_id, account_id, e,
                    )
        logger.info("ChannelLifecycle shutdown complete")
