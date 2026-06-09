"""
channels/gateway — 统一消息网关。

职责
────
  1. Channel 生命周期管理：启动、停止、状态查询
  2. 入站消息路由：接收所有 channel 的入站消息，路由到 Session + Agent
  3. 出站消息路由：通过 OutputTarget + Processor 驱动，所有 channel 走同一套骨架
  4. Session 路由持久化：default_output_targets 记录最后路由，供 Cron / Background Agent 使用

与 OpenClaw 的对应关系
──────────────────────
ChannelGateway.dispatch_inbound()   → OpenClaw dispatchInboundMessage()
ChannelGateway.dispatch_outbound()  → OpenClaw createChannelReplyPipeline() + deliverOutboundPayloads()
ChannelGateway.start_channel()      → OpenClaw channels.start
ChannelGateway.stop_channel()       → OpenClaw channels.logout
ChannelGateway.get_status()         → OpenClaw channels.status

消息流（新出站架构）
──────────────────
入站（Inbound）：
  Discord / 飞书 / WebChat / TUI
         │
         ▼
  ChannelAdapter._dispatch_inbound()
         │
         ▼
  ChannelGateway.dispatch_inbound()
         │
         ├── 查找/创建 Session
         ├── _build_output_target() → OutputTarget + Processor
         ├── session.output_targets / default_output_targets 更新
         ├── _ensure_processor_loop()（每 session 唯一 EventBus 驱动循环）
         └── AgentRunner.run(session, msg, BusEmitter)
                   │
                   ▼
              Agent 循环 → BusEmitter → EventBus.publish(event)
                   │
                   ▼
              processor_loop 收到事件 → _dispatch_event_to_processor()
                   │
                   ▼
              OutputTarget.processor.on_done() / on_token() / on_ask_user() ...
                   │
                   ▼
              adapter.send_text() / SSE 推流 / TUI 打印
"""

import asyncio
from typing import Optional, Callable

from loguru import logger

from .base import (
    ChannelAccountSnapshot,
    InboundMessage,
)
from .output_target import OutputTarget
from .registry import ChannelRegistry
from .config import ChannelConfig
from ccserver.event_bus import AgentEvent, EventType, _VISIBILITY_HIDDEN, _VISIBILITY_DONE_ONLY


async def _dispatch_event_to_processor(target: OutputTarget, event: AgentEvent) -> None:
    """
    将单个 AgentEvent 按 visibility 规则分发到 OutputTarget 的 Processor。

    visibility 过滤规则：
      HIDDEN    → 丢弃所有事件
      DONE_ONLY → 只处理 DONE / ERROR 事件，忽略 TOKEN 等中间事件
      FULL      → 处理所有事件

    ask_user / permission_req 特殊处理：
      - 事件 payload 中携带 asyncio.Future，Processor 负责在用户响应后 set_result()。
      - 多个 OutputTarget 收到同一个 future 时，只有第一个 set_result() 有效（Future 幂等）。

    Args:
        target: 目标 OutputTarget（含 Processor）。
        event:  AgentEvent 实例。
    """
    vis = event.visibility

    # HIDDEN：完全不可见，丢弃
    if vis == _VISIBILITY_HIDDEN:
        return

    t = event.type

    if t == EventType.TOKEN:
        # DONE_ONLY 模式下忽略 token 事件
        if vis != _VISIBILITY_DONE_ONLY:
            await target.processor.on_token(event.payload.get("token", ""), event)

    elif t == EventType.DONE:
        content = event.payload.get("content", "")
        if content:
            await target.processor.on_done(content, event)

    elif t == EventType.ERROR:
        await target.processor.on_error(event.payload.get("error", "unknown error"), event)

    elif t == EventType.ASK_USER:
        future = event.payload.get("future")
        questions = event.payload.get("questions", [])
        if future is not None and not future.done():
            # 闭包捕获 future，防止 set_result 被多次调用
            def make_answer_cb(f):
                def answer_cb(text: str):
                    if not f.done():
                        f.set_result(text)
                return answer_cb
            await target.processor.on_ask_user(questions, make_answer_cb(future))

    elif t == EventType.PERMISSION_REQ:
        future = event.payload.get("future")
        tool_name = event.payload.get("tool_name", "")
        tool_input = event.payload.get("tool_input", {})
        if future is not None and not future.done():
            def make_grant_cb(f):
                def grant_cb(approved: bool):
                    if not f.done():
                        f.set_result(approved)
                return grant_cb
            await target.processor.on_permission_request(tool_name, tool_input, make_grant_cb(future))


class ChannelGateway:
    """
    统一消息网关。

    是 channel 系统的"控制平面"，协调所有 channel 适配器与 ccserver 核心之间的消息流。
    新出站架构：所有 channel（飞书/Discord/WebUI/TUI）走同一套 OutputTarget + Processor 骨架，
    没有特判，没有 OutboundBus 桥接层。

    消息流（新架构）：
      InboundMessage
        → dispatch_inbound()
          → 找/创建 Session
          → 组装 OutputTarget（含 Processor）
          → 更新 session.output_targets / default_output_targets
          → 启动 EventBus 订阅循环（驱动 Processor）
          → 启动 Agent.run()（使用 BusEmitter）

    Attributes:
        registry:        ChannelRegistry 实例
        session_manager: SessionManager 实例，用于创建/查找 Session
        runner:          AgentRunner 实例，用于启动 Agent
        config:          ChannelConfig 实例，管理 channels.json
        _running:        channel_id -> account_id -> info 的映射
        _processor_tasks: session_id -> asyncio.Task（EventBus → Processor 驱动任务）
    """

    def __init__(
        self,
        registry: ChannelRegistry,
        session_manager,
        runner,
        config: ChannelConfig | None = None,
    ):
        self.registry = registry
        self.session_manager = session_manager
        self.runner = runner
        self.config = config or ChannelConfig()

        # channel_id -> account_id -> {adapter, config, snapshot}
        self._running: dict[str, dict[str, dict]] = {}

        # session_id -> asyncio.Task（EventBus → Processor 驱动循环）
        self._processor_tasks: dict[str, asyncio.Task] = {}

        logger.info(
            "ChannelGateway initialized | registry_size={} config={}",
            len(registry), self.config.config_path,
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
    #  Push 路由注册（用于定时任务 / 后台 Agent 主动推送）
    # ═════════════════════════════════════════════════════════════════════════════

    async def register_push_route(
        self,
        session_id: str,
        channel_id: str,
        account_id: str,
        to: str,
        chat_type: str = "direct",
        thread_id: str | None = None,
        reply_to_id: str | None = None,
    ) -> None:
        """
        预注册推送路由（用于 Cron / Background Agent 的 default_output_targets）。

        当 channel adapter 启动时，可调用本方法预先绑定"定时任务触发时
        把消息发给谁"的路由信息。不需要等待用户发消息触发 dispatch_inbound。

        典型使用场景：
          - Discord/飞书 Bot 启动后，将"管理频道"绑定为推送目标
          - TUI 启动后注册 session，确保定时任务回复能路由到正确的 session

        Args:
            session_id:   目标 Session ID。
            channel_id:   channel 标识（如 "discord", "feishu"）。
            account_id:   Bot 账户标识。
            to:           接收推送的目标 ID（用户 ID 或频道/群 ID）。
            chat_type:    消息类型，"direct" 或 "group"。
            thread_id:    群组 ID（chat_type="group" 时使用）。
            reply_to_id:  回复引用的消息 ID（可选）。
        """
        session = self.session_manager.get(session_id)
        if session is None:
            logger.warning(
                "register_push_route: session not found | id={} channel={}",
                session_id[:8], channel_id,
            )
            return

        # 构建 OutputTarget + Processor 并写入 default_output_targets
        adapter = self.registry.get_adapter(channel_id)
        if adapter is None:
            logger.warning(
                "register_push_route: adapter not found | channel={}",
                channel_id,
            )
            return

        target = OutputTarget(
            channel_id=channel_id,
            account_id=account_id,
            to=to,
            reply_to_id=reply_to_id,
            processor=None,
        )
        target.processor = adapter.build_processor(target)

        # default_output_targets：持久化路由，供 Cron 触发使用
        session.default_output_targets = [target]

        # 同时确保 EventBus 驱动循环已启动
        await self._ensure_processor_loop(session_id)

        logger.info(
            "Push route registered | session={} channel={} to={}",
            session_id[:8], channel_id, to,
        )

    # ═════════════════════════════════════════════════════════════════════════════
    #  入站消息处理
    # ═════════════════════════════════════════════════════════════════════════════

    async def dispatch_inbound(self, msg: InboundMessage) -> None:
        """
        所有 channel 适配器的入站消息统一入口。

        与 OpenClaw 的 dispatchInboundMessage() 对应。

        处理流程：
          1. 查找或创建 Session
          2. 将用户消息追加到 session
          3. 构建 OutputTarget + Processor（所有 channel 统一走此路径）
          4. 更新 session.output_targets 和 default_output_targets
          5. 启动 EventBus → Processor 驱动循环（每个 session 唯一）
          6. 创建 BusEmitter（Agent 的唯一 emitter）
          7. 异步启动 Agent.run()

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

        # 4. 构建 OutputTarget + Processor（所有 channel 统一走此路径）
        target = self._build_output_target(msg, session)

        # 5. 更新 session.output_targets（当前轮次）和 default_output_targets（持久化路由）
        session.output_targets = [target]
        session.default_output_targets = [target]

        # 6. 通知 Processor 新一轮开始
        await target.processor.on_turn_start()

        # 7. 确保 EventBus → Processor 驱动循环已启动（每个 session 唯一一个循环）
        await self._ensure_processor_loop(session.id)

        # 8. 创建 BusEmitter（Agent 的唯一 emitter）
        from ccserver.emitters.bus_emitter import BusEmitter
        bus_emitter = BusEmitter(
            bus=session.event_bus,
            agent_id=f"gateway_{session.id[:8]}",
            session_id=session.id,
            sender_type="gateway",
        )

        logger.info(
            "Starting agent for inbound | session={} channel={} sender={}",
            session.id[:8], msg.channel_id, msg.sender_id,
        )

        # 9. 异步启动 Agent（不阻塞，让 HTTP handler 立即返回）
        async def _run_agent():
            try:
                await self.runner.run(session, msg.text, bus_emitter)
            except Exception as e:
                logger.error(
                    "Agent run failed | session={} err={}",
                    session.id[:8], e,
                )
                await self._send_error_reply(session.id, str(e))

        asyncio.create_task(_run_agent())

    def _build_output_target(self, msg: InboundMessage, session) -> OutputTarget:
        """
        根据入站消息构建 OutputTarget + Processor。

        所有 channel（飞书/Discord/WebUI/TUI）均走此路径，没有特判。
        Processor 由各 adapter 的 build_processor() 工厂方法创建。

        Args:
            msg:     入站消息。
            session: Session 实例（仅用于日志）。

        Returns:
            带 processor 的 OutputTarget 实例。
        """
        # 目标 ID：私聊用 sender_id，群聊用 thread_id 或 sender_id
        to = msg.sender_id if msg.chat_type == "direct" else (msg.thread_id or msg.sender_id)

        # 创建 OutputTarget（processor 先占位，下一步由 adapter 填充）
        target = OutputTarget(
            channel_id=msg.channel_id,
            account_id=msg.account_id,
            to=to,
            reply_to_id=msg.message_id,
            processor=None,
        )

        adapter = self.registry.get_adapter(msg.channel_id)
        if adapter is not None:
            # 委托给 adapter 创建对应的 Processor
            target.processor = adapter.build_processor(target)
        else:
            # 未知 channel，使用无操作 Processor（只记录警告）
            from ccserver.channels.processor import Processor as NoOpProcessor
            target.processor = NoOpProcessor()
            logger.warning(
                "_build_output_target: adapter not found, using no-op Processor | channel={}",
                msg.channel_id,
            )

        return target

    async def _ensure_processor_loop(self, session_id: str) -> None:
        """
        确保指定 session 的 EventBus → Processor 驱动循环已启动。

        每个 session 只创建一个驱动循环任务，任务持续运行直到 session 被清理。
        循环收到 EventBus 事件后，遍历 session.output_targets，
        按 visibility 过滤后分发到各 Processor 的对应 on_* 方法。

        Args:
            session_id: Session ID
        """
        existing = self._processor_tasks.get(session_id)
        if existing is not None and not existing.done():
            # 已有运行中的循环，无需重复创建
            return

        session = self.session_manager.get(session_id)
        if session is None:
            logger.warning(
                "_ensure_processor_loop: session not found | id={}",
                session_id[:8],
            )
            return

        async def _loop():
            """
            EventBus 监听协程。

            持续从 EventBus 获取事件，分发到所有 output_targets 的 Processor。
            当收到 DONE / ERROR / CANCELLED 事件后，通知 Processor on_turn_end()。
            """
            subscriber_id = f"gateway_proc_{session_id[:8]}"
            try:
                async with session.event_bus.subscribe(subscriber_id) as sub:
                    while True:
                        try:
                            event = await sub.get(timeout=5.0)
                        except asyncio.CancelledError:
                            break
                        if event is None:
                            continue

                        # 取当前 output_targets（每次取最新，支持动态更新）
                        targets = list(session.output_targets)
                        for target in targets:
                            try:
                                await _dispatch_event_to_processor(target, event)
                            except Exception as e:
                                logger.error(
                                    "Processor dispatch error | session={} channel={} err={}",
                                    session_id[:8], target.channel_id, e,
                                )

                        # 轮次结束信号：通知所有 Processor
                        if event.type in (EventType.DONE, EventType.ERROR, EventType.CANCELLED):
                            for target in targets:
                                try:
                                    await target.processor.on_turn_end()
                                except Exception as e:
                                    logger.error(
                                        "on_turn_end error | session={} channel={} err={}",
                                        session_id[:8], target.channel_id, e,
                                    )

            except Exception as e:
                logger.error(
                    "Processor loop error | session={} err={}",
                    session_id[:8], e,
                )

        task = asyncio.create_task(_loop())
        self._processor_tasks[session_id] = task
        logger.debug(
            "Processor loop started | session={}",
            session_id[:8],
        )

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


    # ═════════════════════════════════════════════════════════════════════════════
    #  出站消息处理（新架构：通过 _ensure_processor_loop + _dispatch_event_to_processor 驱动）
    # ═════════════════════════════════════════════════════════════════════════════

    async def _send_error_reply(self, session_id: str, error_msg: str) -> None:
        """
        Agent 运行失败时，通过 default_output_targets 向用户发送错误提示。

        Args:
            session_id: Session ID
            error_msg: 错误信息
        """
        session = self.session_manager.get(session_id)
        if session is None:
            return

        targets = session.default_output_targets or session.output_targets
        text = f"抱歉，处理消息时出错了：{error_msg}"

        for target in targets:
            try:
                adapter = self.registry.get_adapter(target.channel_id)
                if adapter is not None:
                    await adapter.send_text(
                        target.account_id,
                        target.to,
                        text,
                        reply_to_id=target.reply_to_id,
                    )
            except Exception as e:
                logger.error(
                    "Failed to send error reply | session={} channel={} err={}",
                    session_id[:8], target.channel_id, e,
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
        手动发送出站消息（API 调用 / 后台推送）。

        优先使用 session.default_output_targets 中的第一个目标。

        Args:
            session_id: Session ID
            text: 消息文本
            media_urls: 媒体 URL 列表（暂未使用）

        Returns:
            发送结果字典
        """
        session = self.session_manager.get(session_id)
        if session is None:
            return {"success": False, "error": "Session not found"}

        targets = session.default_output_targets or session.output_targets
        if not targets:
            logger.warning(
                "No output targets for manual outbound | session={}",
                session_id[:8],
            )
            return {"success": False, "error": "No output targets found"}

        target = targets[0]
        adapter = self.registry.get_adapter(target.channel_id)
        if adapter is None:
            return {"success": False, "error": f"Adapter not found: {target.channel_id}"}

        return await adapter.send_text(
            target.account_id,
            target.to,
            text,
            reply_to_id=target.reply_to_id,
        )

    # ═════════════════════════════════════════════════════════════════════════════
    #  清理
    # ═════════════════════════════════════════════════════════════════════════════

    async def cleanup_session(self, session_id: str) -> None:
        """
        清理指定 session 的资源。

        取消 EventBus → Processor 驱动循环任务。

        Args:
            session_id: Session ID
        """
        task = self._processor_tasks.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        logger.debug("Session cleaned up | id={}", session_id[:8])

    async def shutdown(self) -> None:
        """
        关闭所有 channel 连接，释放资源。

        在服务器关闭时调用。
        """
        logger.info("ChannelGateway shutting down...")

        # 取消所有 Processor 驱动循环任务
        for session_id, task in list(self._processor_tasks.items()):
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._processor_tasks.clear()

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

        logger.info("ChannelGateway shutdown complete")
