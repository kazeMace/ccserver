"""
channels/gateway — 统一消息网关。

职责
────
  1. Channel 生命周期管理：启动、停止、状态查询
  2. 入站消息路由：接收所有 channel 的入站消息，路由到 Session + Agent
  3. 出站消息路由：监听 Agent 的 DONE 事件，自动发送回复到对应的 channel
  4. Session 路由记录：保存"最后使用的 channel"，用于自动回复路由

与 OpenClaw 的对应关系
──────────────────────
ChannelGateway.dispatch_inbound()  → OpenClaw dispatchInboundMessage()
ChannelGateway.dispatch_outbound()  → OpenClaw createChannelReplyPipeline() + deliverOutboundPayloads()
ChannelGateway._routes              → OpenClaw updateLastRoute
ChannelGateway.start_channel()      → OpenClaw channels.start
ChannelGateway.stop_channel()       → OpenClaw channels.logout
ChannelGateway.get_status()         → OpenClaw channels.status

消息流
──────
入站（Inbound）：
  Discord/Telegram/飞书/钉钉/WebChat
         │
         ▼
  ChannelAdapter._dispatch_inbound()
         │
         ▼
  ChannelGateway.dispatch_inbound()
         │
         ├── 查找/创建 Session
         ├── 记录路由信息（_routes）
         ├── 订阅 EventBus（监听 DONE 事件）
         └── 启动 AgentRunner.run()
                   │
                   ▼
              Agent 循环 → EventBus.publish(DONE)
                   │
                   ▼
              EventBus 订阅者收到 DONE
                   │
                   ▼
              ChannelGateway._on_agent_done()
                   │
                   ▼
              ChannelAdapter.send_message()
                   │
                   ▼
              Discord/Telegram/飞书/钉钉/WebChat

出站（Outbound）：
  与入站相同，DONE 事件触发回复发送。
"""

import asyncio
from typing import Optional, Callable

from loguru import logger

from .base import (
    BaseChannelAdapter,
    ChannelAccountSnapshot,
    InboundMessage,
    OutboundMessage,
)
from .registry import ChannelRegistry
from .config import ChannelConfig
from ccserver.event_bus import AgentEvent, EventType
from ccserver.outbound_bus import OutboundBus, OutboundEvent


class ChannelGateway:
    """
    统一消息网关。

    是 channel 系统的"控制平面"，协调所有 channel 适配器与 ccserver 核心之间的消息流。

    Attributes:
        registry:        ChannelRegistry 实例
        session_manager: SessionManager 实例，用于创建/查找 Session
        runner:          AgentRunner 实例，用于启动 Agent
        config:          ChannelConfig 实例，管理 channels.json
        outbound_bus:    OutboundBus 实例，用于解耦出站回复
        _routes:         session_id -> route info 的映射
        _running:        channel_id -> account_id -> info 的映射
        _event_tasks:    session_id -> asyncio.Task 的映射（EventBus 监听任务）
    """

    def __init__(
        self,
        registry: ChannelRegistry,
        session_manager,
        runner,
        outbound_bus=None,
        config: ChannelConfig | None = None,
    ):
        self.registry = registry
        self.session_manager = session_manager
        self.runner = runner
        self.outbound_bus = outbound_bus
        self.config = config or ChannelConfig()

        # session_id -> 路由信息（与 OpenClaw 的 updateLastRoute 对应）
        self._routes: dict[str, dict] = {}

        # channel_id -> account_id -> {adapter, config, snapshot}
        self._running: dict[str, dict[str, dict]] = {}

        # session_id -> asyncio.Task（EventBus 监听任务）
        self._event_tasks: dict[str, asyncio.Task] = {}

        # session_id -> outbound handler（用于清理时 unsubscribe）
        self._outbound_handlers: dict[str, Callable] = {}

        logger.info(
            "ChannelGateway initialized | registry_size={} outbound_bus={} config={}",
            len(registry), outbound_bus is not None, self.config.config_path,
        )

    # ═════════════════════════════════════════════════════════════════════════════
    #  生命周期管理
    # ═════════════════════════════════════════════════════════════════════════════

    async def start_channel(
        self,
        channel_id: str,
        account_id: str,
        config: dict,
    ) -> ChannelAccountSnapshot:
        """
        启动一个 channel 账户。

        与 OpenClaw 的 channels.start 对应。

        Args:
            channel_id: channel ID 或别名，如 "discord"
            account_id: 账户标识，如 bot 用户名
            config:     配置字典，内容因平台而异

        Returns:
            启动后的账户状态快照

        Raises:
            ValueError: 如果 channel_id 未知
            RuntimeError: 如果启动失败
        """
        adapter = self.registry.get_adapter(channel_id)
        if adapter is None:
            known = list(self.registry._adapters.keys())
            raise ValueError(
                f"Unknown channel '{channel_id}'. Known: {known}"
            )

        canonical = self.registry.normalize_channel_id(channel_id)
        assert canonical is not None

        # 注册入站消息回调
        adapter.set_inbound_handler(self.dispatch_inbound)

        # 启动适配器
        logger.info(
            "Starting channel | channel={} account={}",
            canonical, account_id,
        )
        snapshot = await adapter.start(account_id, config)

        # 记录运行状态
        self._running.setdefault(canonical, {})[account_id] = {
            "adapter": adapter,
            "config": config,
            "snapshot": snapshot,
        }

        logger.info(
            "Channel started | channel={} account={} running={} connected={}",
            canonical, account_id, snapshot.running, snapshot.connected,
        )
        return snapshot

    async def stop_channel(self, channel_id: str, account_id: str) -> None:
        """
        停止一个 channel 账户。

        与 OpenClaw 的 channels.logout 对应。

        Args:
            channel_id: channel ID 或别名
            account_id: 账户标识
        """
        adapter = self.registry.get_adapter(channel_id)
        if adapter is None:
            logger.warning("stop_channel: unknown channel '{}'", channel_id)
            return

        canonical = self.registry.normalize_channel_id(channel_id)
        assert canonical is not None

        logger.info(
            "Stopping channel | channel={} account={}",
            canonical, account_id,
        )

        try:
            await adapter.stop(account_id)
        except Exception as e:
            logger.error(
                "Channel stop failed | channel={} account={} err={}",
                canonical, account_id, e,
            )

        # 清理运行状态
        if canonical in self._running:
            self._running[canonical].pop(account_id, None)
            if not self._running[canonical]:
                del self._running[canonical]

        logger.info("Channel stopped | channel={} account={}", canonical, account_id)

    async def get_status(
        self,
        channel_id: str,
        account_id: str,
    ) -> ChannelAccountSnapshot:
        """
        查询 channel 账户状态。

        与 OpenClaw 的 channels.status 对应。

        Args:
            channel_id: channel ID 或别名
            account_id: 账户标识

        Returns:
            账户状态快照
        """
        adapter = self.registry.get_adapter(channel_id)
        if adapter is None:
            raise ValueError(f"Unknown channel '{channel_id}'")

        return await adapter.get_status(account_id)

    def list_running(self) -> list[dict]:
        """
        列出所有正在运行的 channel 账户。

        Returns:
            运行中的 channel 账户列表
        """
        result = []
        for channel_id, accounts in self._running.items():
            for account_id, info in accounts.items():
                snapshot = info.get("snapshot")
                if snapshot:
                    result.append({
                        "channel_id": channel_id,
                        "account_id": account_id,
                        "status": snapshot.to_dict(),
                    })
        return result

    # ═════════════════════════════════════════════════════════════════════════════
    #  入站消息处理
    # ═════════════════════════════════════════════════════════════════════════════

    async def dispatch_inbound(self, msg: InboundMessage) -> None:
        """
        所有 channel 适配器的入站消息统一入口。

        与 OpenClaw 的 dispatchInboundMessage() 对应。

        处理流程：
          1. 查找或创建 Session
          2. 记录路由信息（_routes）
          3. 启动 EventBus 订阅（监听 DONE 事件，用于自动回复）
          4. 启动 Agent 处理消息

        Args:
            msg: 统一入站消息格式
        """
        assert msg.channel_id, "InboundMessage.channel_id is required"
        assert msg.account_id, "InboundMessage.account_id is required"
        assert msg.sender_id, "InboundMessage.sender_id is required"

        # 1. 解析 session key
        session_key = self._resolve_session_key(msg)
        logger.debug(
            "Dispatch inbound | channel={} sender={} session_key={}",
            msg.channel_id, msg.sender_id, session_key,
        )

        # 2. 查找或创建 Session
        session = self.session_manager.get(session_key)
        if session is None:
            session = self.session_manager.create(session_key)
            logger.info(
                "Session auto-created for inbound | key={} channel={}",
                session_key, msg.channel_id,
            )

        # 3. 将用户消息追加到 session
        session.append_message({
            "role": "user",
            "content": msg.text,
            "_meta": {
                "channel_id": msg.channel_id,
                "account_id": msg.account_id,
                "sender_id": msg.sender_id,
                "sender_name": msg.sender_name,
                "chat_type": msg.chat_type,
                "thread_id": msg.thread_id,
                "message_id": msg.message_id,
                "timestamp": msg.timestamp,
            }
        })

        # 4. 保存路由信息（用于出站回复）
        self._set_route(session.id, msg)

        # 5. 订阅 OutboundBus（外部 channel adapter 接收回复事件）
        #    WebChat 不走 OutboundBus（SSE/WS 已通过 EventBus 接收流式事件）
        if self.outbound_bus and msg.channel_id != "webchat":
            await self._subscribe_outbound(session.id, msg)

        # 6. 启动 EventBus 订阅（监听 DONE 事件，触发 OutboundBus 发布）
        await self._ensure_event_subscription(session.id)

        # 6. 启动 Agent
        logger.info(
            "Starting agent for inbound | session={} channel={} sender={}",
            session.id[:8], msg.channel_id, msg.sender_id,
        )

        # 创建 emitter：使用 CollectEmitter 收集输出，同时通过 BusEmitter 广播到 EventBus
        # 注意：对于外部 channel（非 webchat），我们需要一个 emitter 来收集最终回复
        from ccserver.emitters.collect import CollectEmitter
        from ccserver.emitters.bus_emitter import BusEmitter

        collect_emitter = CollectEmitter()
        bus_emitter = BusEmitter(
            bus=session.event_bus,
            agent_id=f"gateway_{session.id[:8]}",
            session_id=session.id,
            sender_type="gateway",
        )

        # 使用一个组合 emitter：同时写入 CollectEmitter 和 BusEmitter
        # 这样 WebChat 客户端（订阅 EventBus）也能收到事件
        from ccserver.emitters import BaseEmitter

        class _ComboEmitter(BaseEmitter):
            """同时向 CollectEmitter 和 BusEmitter 发送事件的组合 emitter。"""

            def __init__(self, *emitters):
                self._emitters = emitters

            async def emit(self, event: dict) -> None:
                for e in self._emitters:
                    await e.emit(event)

        combo = _ComboEmitter(collect_emitter, bus_emitter)

        # 启动 Agent（不阻塞，让 HTTP handler 立即返回）
        async def _run_agent():
            try:
                await self.runner.run(session, msg.text, combo)
            except Exception as e:
                logger.error(
                    "Agent run failed | session={} err={}",
                    session.id[:8], e,
                )
                # 发送错误回复到用户
                await self._send_error_reply(session.id, str(e))

        asyncio.create_task(_run_agent())

    def _resolve_session_key(self, msg: InboundMessage) -> str:
        """
        根据入站消息解析 session key。

        策略：
          - DM（私聊）："{channel_id}:{account_id}:{sender_id}"
          - 群聊："{channel_id}:{account_id}:group:{thread_id}"

        这样不同平台、不同群组的用户互不干扰。

        Args:
            msg: 入站消息

        Returns:
            session key 字符串
        """
        if msg.chat_type == "direct":
            return f"{msg.channel_id}:{msg.account_id}:{msg.sender_id}"
        else:
            # 群聊：使用 thread_id（群组 ID）作为 session 的一部分
            thread_part = msg.thread_id or msg.sender_id
            return f"{msg.channel_id}:{msg.account_id}:group:{thread_part}"

    async def _subscribe_outbound(self, session_id: str, msg: InboundMessage) -> None:
        """
        为外部 channel adapter 订阅 OutboundBus。

        WebChat 不走 OutboundBus（SSE/WS 已通过 EventBus 接收流式事件），
        因此只有外部平台（飞书/钉钉/QQ/Discord 等）才需要订阅。

        Args:
            session_id: Session ID
            msg: 入站消息（用于获取 channel_id 和 route 信息）
        """
        if self.outbound_bus is None:
            return

        # 如果该 session 已经订阅过 OutboundBus，跳过（避免重复订阅导致消息重复发送）
        if session_id in self._outbound_handlers:
            return

        adapter = self.registry.get_adapter(msg.channel_id)
        if adapter is None:
            return

        # 构建 handler：闭包捕获 route 信息
        route = self._routes.get(session_id, {})

        async def _handler(event: OutboundEvent) -> None:
            """OutboundBus handler：调用 adapter 发送回复。"""
            if not event.is_final:
                return  # 默认只处理最终回复

            if not event.text and not event.media_urls:
                return

            out_msg = OutboundMessage(
                text=event.text,
                media_urls=event.media_urls,
                reply_to_id=event.reply_to_id or route.get("reply_to_id"),
                thread_id=route.get("thread_id"),
            )

            try:
                result = await adapter.send_message(
                    route.get("account_id", msg.account_id),
                    route.get("to", msg.sender_id),
                    out_msg,
                )
                logger.info(
                    "OutboundBus reply sent | channel={} session={} success={}",
                    msg.channel_id, session_id[:8], result.get("success"),
                )
            except Exception as e:
                logger.error(
                    "OutboundBus reply failed | channel={} session={} err={}",
                    msg.channel_id, session_id[:8], e,
                )

        self.outbound_bus.subscribe(session_id, _handler)
        self._outbound_handlers[session_id] = _handler

        logger.debug(
            "OutboundBus subscribed | session={} channel={}",
            session_id[:8], msg.channel_id,
        )

    def _set_route(self, session_id: str, msg: InboundMessage) -> None:
        """
        记录 session 的最后路由信息，用于出站回复。

        与 OpenClaw 的 updateLastRoute 对应。

        Args:
            session_id: Session ID
            msg: 入站消息
        """
        self._routes[session_id] = {
            "channel_id": msg.channel_id,
            "account_id": msg.account_id,
            "to": msg.sender_id if msg.chat_type == "direct" else (msg.thread_id or msg.sender_id),
            "chat_type": msg.chat_type,
            "thread_id": msg.thread_id,
            "sender_id": msg.sender_id,
            "sender_name": msg.sender_name,
            "reply_to_id": msg.message_id,  # 回复时引用原消息
            "timestamp": msg.timestamp,
        }
        logger.debug(
            "Route saved | session={} channel={} to={}",
            session_id[:8], msg.channel_id, self._routes[session_id]["to"],
        )

    # ═════════════════════════════════════════════════════════════════════════════
    #  出站消息处理
    # ═════════════════════════════════════════════════════════════════════════════

    async def _ensure_event_subscription(self, session_id: str) -> None:
        """
        确保指定 session 的 EventBus 有订阅者监听 DONE 事件。

        每个 session 只创建一个监听任务。任务持续运行直到 session 被清理。

        Args:
            session_id: Session ID
        """
        if session_id in self._event_tasks:
            # 已有订阅，无需重复创建
            return

        session = self.session_manager.get(session_id)
        if session is None:
            logger.warning(
                "Cannot subscribe: session not found | id={}",
                session_id[:8],
            )
            return

        async def _event_loop():
            """
            EventBus 监听协程。

            持续监听 session 的 EventBus，收到 DONE 事件后
            调用 _on_agent_done() 发送回复到对应的 channel。
            """
            subscriber_id = f"gateway_{session_id[:8]}"

            try:
                async with session.event_bus.subscribe(
                    subscriber_id,
                    filter_fn=lambda e: e.type == EventType.DONE,
                ) as sub:
                    while True:
                        try:
                            event = await sub.get(timeout=5.0)
                        except asyncio.CancelledError:
                            break
                        if event is None:
                            # 超时，继续循环
                            continue

                        # 处理 DONE 事件
                        await self._on_agent_done(session_id, event)
            except Exception as e:
                logger.error(
                    "Event subscription error | session={} err={}",
                    session_id[:8], e,
                )

        task = asyncio.create_task(_event_loop())
        self._event_tasks[session_id] = task
        logger.debug(
            "EventBus subscription created | session={}",
            session_id[:8],
        )

    async def _on_agent_done(self, session_id: str, event: AgentEvent) -> None:
        """
        Agent 完成时的事件处理。

        将回复发布到 OutboundBus，由订阅了该 session 的 adapter 发送。
        如果 OutboundBus 不可用（或未配置），则回退到直接发送。

        Args:
            session_id: Session ID
            event: DONE 事件
        """
        route = self._routes.get(session_id)
        if not route:
            logger.warning(
                "No route found for session, skipping reply | session={}",
                session_id[:8],
            )
            return

        content = event.payload.get("content", "")
        if not content:
            logger.debug(
                "Empty reply content, skipping | session={}",
                session_id[:8],
            )
            return

        logger.info(
            "Agent done | session={} channel={} to={} text_len={}",
            session_id[:8], route["channel_id"], route["to"], len(content),
        )

        # 方案 A：通过 OutboundBus 发布（推荐，解耦）
        if self.outbound_bus and self.outbound_bus.has_subscribers(session_id):
            await self.outbound_bus.publish(OutboundEvent(
                session_id=session_id,
                text=content,
                is_final=True,
                reply_to_id=route.get("reply_to_id"),
            ))
            return

        # 方案 B：回退到直接发送（兼容模式，OutboundBus 未配置时）
        logger.debug(
            "OutboundBus not available, falling back to direct send | session={}",
            session_id[:8],
        )
        try:
            await self._send_reply(
                session_id=session_id,
                channel_id=route["channel_id"],
                account_id=route["account_id"],
                to=route["to"],
                text=content,
                reply_to_id=route.get("reply_to_id"),
                thread_id=route.get("thread_id"),
            )
        except Exception as e:
            logger.error(
                "Reply send failed | session={} channel={} err={}",
                session_id[:8], route["channel_id"], e,
            )

    async def _send_reply(
        self,
        session_id: str,
        channel_id: str,
        account_id: str,
        to: str,
        text: str,
        reply_to_id: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> None:
        """
        发送回复到指定 channel。

        Args:
            session_id: Session ID（用于日志）
            channel_id: channel ID
            account_id: 账户标识
            to: 目标用户/群组 ID
            text: 回复文本
            reply_to_id: 回复哪条消息
            thread_id: 线程 ID
        """
        adapter = self.registry.get_adapter(channel_id)
        if adapter is None:
            logger.error(
                "Adapter not found for reply | channel={}",
                channel_id,
            )
            return

        msg = OutboundMessage(
            text=text,
            reply_to_id=reply_to_id,
            thread_id=thread_id,
        )

        result = await adapter.send_message(account_id, to, msg)

        if result.get("success"):
            logger.info(
                "Reply sent | session={} channel={} to={} chunks={}",
                session_id[:8], channel_id, to, result.get("total_count", 1),
            )
        else:
            logger.error(
                "Reply failed | session={} channel={} to={} errors={}",
                session_id[:8], channel_id, to,
                [r.get("error") for r in result.get("results", []) if not r.get("success")],
            )

    async def _send_error_reply(self, session_id: str, error_msg: str) -> None:
        """
        Agent 运行失败时，向用户发送错误提示。

        Args:
            session_id: Session ID
            error_msg: 错误信息
        """
        route = self._routes.get(session_id)
        if not route:
            return

        text = f"抱歉，处理消息时出错了：{error_msg}"
        try:
            await self._send_reply(
                session_id=session_id,
                channel_id=route["channel_id"],
                account_id=route["account_id"],
                to=route["to"],
                text=text,
            )
        except Exception as e:
            logger.error(
                "Failed to send error reply | session={} err={}",
                session_id[:8], e,
            )

    # ═════════════════════════════════════════════════════════════════════════════
    #  配置联动：自动扫描与自动启动
    # ═════════════════════════════════════════════════════════════════════════════

    async def auto_discover_and_start(self) -> dict:
        """
        自动扫描适配器并启动配置中 enabled + auto_start 的 channel。

        这是 server.py 启动时调用的主入口，完成三件事：
          1. 加载 channels.json 配置
          2. 扫描 adapters 目录自动注册所有适配器
          3. 启动配置中标记为 auto_start 的 channel

        Returns:
            {
                "discovered": int,     # 新发现的适配器数量
                "started": int,        # 成功自动启动的数量
                "failed": int,         # 启动失败的数量
                "skipped": int,        # 配置中未启用或无账户的数量
            }
        """
        # 1. 加载配置
        self.config.load()

        # 2. 自动扫描适配器
        discovered = self.registry.discover()

        # 3. 自动启动
        started = 0
        failed = 0
        skipped = 0

        for channel_id, account_id, account_cfg in self.config.list_auto_start():
            if not self.registry.is_registered(channel_id):
                logger.warning(
                    "Auto-start skipped: adapter not registered | channel={}",
                    channel_id,
                )
                skipped += 1
                continue

            logger.info(
                "Auto-starting channel | channel={} account={}",
                channel_id, account_id,
            )
            try:
                await self.start_channel(channel_id, account_id, account_cfg)
                started += 1
            except Exception as e:
                logger.error(
                    "Auto-start failed | channel={} account={} err={}",
                    channel_id, account_id, e,
                )
                failed += 1

        result = {
            "discovered": discovered,
            "started": started,
            "failed": failed,
            "skipped": skipped,
        }
        logger.info(
            "Channel auto-start complete | discovered={} started={} failed={} skipped={}",
            discovered, started, failed, skipped,
        )
        return result

    # ═════════════════════════════════════════════════════════════════════════════
    #  手动出站（API 调用）
    # ═════════════════════════════════════════════════════════════════════════════

    async def dispatch_outbound(
        self,
        session_id: str,
        text: str,
        media_urls: Optional[list[str]] = None,
    ) -> dict:
        """
        手动发送出站消息。

        用于 API 调用或后台任务主动向用户推送消息。

        Args:
            session_id: Session ID
            text: 消息文本
            media_urls: 媒体 URL 列表

        Returns:
            发送结果字典
        """
        route = self._routes.get(session_id)
        if not route:
            logger.warning(
                "No route for manual outbound | session={}",
                session_id[:8],
            )
            return {"success": False, "error": "No route found"}

        msg = OutboundMessage(
            text=text,
            media_urls=media_urls or [],
            thread_id=route.get("thread_id"),
        )

        adapter = self.registry.get_adapter(route["channel_id"])
        if adapter is None:
            return {"success": False, "error": f"Adapter not found: {route['channel_id']}"}

        return await adapter.send_message(
            route["account_id"],
            route["to"],
            msg,
        )

    # ═════════════════════════════════════════════════════════════════════════════
    #  清理
    # ═════════════════════════════════════════════════════════════════════════════

    async def cleanup_session(self, session_id: str) -> None:
        """
        清理指定 session 的资源。

        取消 EventBus 订阅任务，取消 OutboundBus 订阅，删除路由信息。

        Args:
            session_id: Session ID
        """
        # 取消 EventBus 监听任务
        task = self._event_tasks.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # 取消 OutboundBus 订阅
        handler = self._outbound_handlers.pop(session_id, None)
        if handler is not None and self.outbound_bus is not None:
            self.outbound_bus.unsubscribe(session_id, handler)

        # 删除路由信息
        self._routes.pop(session_id, None)

        logger.debug("Session cleaned up | id={}", session_id[:8])

    async def shutdown(self) -> None:
        """
        关闭所有 channel 连接，释放资源。

        在服务器关闭时调用。
        """
        logger.info("ChannelGateway shutting down...")

        # 取消所有 EventBus 监听任务
        for session_id, task in list(self._event_tasks.items()):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._event_tasks.clear()

        # 取消所有 OutboundBus 订阅
        if self.outbound_bus is not None:
            for session_id, handler in list(self._outbound_handlers.items()):
                self.outbound_bus.unsubscribe(session_id, handler)
        self._outbound_handlers.clear()

        # 停止所有 channel
        for channel_id, accounts in list(self._running.items()):
            for account_id in list(accounts.keys()):
                try:
                    await self.stop_channel(channel_id, account_id)
                except Exception as e:
                    logger.error(
                        "Shutdown error | channel={} account={} err={}",
                        channel_id, account_id, e,
                    )

        self._routes.clear()
        logger.info("ChannelGateway shutdown complete")
