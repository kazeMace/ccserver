"""
channels/gateway — 统一消息网关门面。

职责：组装子模块，对外暴露统一 API，自身不含业务逻辑。

子模块：
  ChannelLifecycle       Channel 生命周期（start/stop/status/auto_discover）
  ProcessorLoopManager   EventBus → Processor 驱动循环生命周期
  OutboundDispatcher     手动出站投递（API 调用/后台推送）
  GatewayCommandHandler  Gateway 层内置控制命令（/stop /new /help 等）

消息流：
  InboundMessage
    → dispatch_inbound()
      → GatewayCommandHandler（/stop /new /reset /status /help 直接处理，不进 Agent）
      → 并发守护（session_lock + pending 队列）
      → 查找/创建 Session → 构建 OutputTarget → ProcessorLoopManager.ensure()
      → asyncio.create_task(runner.run(...))

与 OpenClaw 的对应关系：
  dispatch_inbound()   → dispatchInboundMessage()
  dispatch_outbound()  → deliverOutboundPayloads()
  start_channel()      → channels.start
  stop_channel()       → channels.logout
  get_status()         → channels.status
"""

import asyncio
from typing import Optional

from loguru import logger

from .base import ChannelAccountSnapshot, InboundMessage
from .output_target import OutputTarget
from .registry import ChannelRegistry
from .config import ChannelConfig
from .lifecycle import ChannelLifecycle
from .processor_loop import ProcessorLoopManager
from .outbound import OutboundDispatcher
from .gateway_commands import GatewayCommandHandler


class ChannelGateway:
    """
    统一消息网关门面。

    只做子模块组装和方法委托，不含业务逻辑。
    所有业务逻辑分散到：lifecycle / processor_loop / outbound。

    Attributes:
        registry:        ChannelRegistry 实例（适配器注册）
        session_manager: SessionManager 实例
        runner:          AgentRunner 实例
        config:          ChannelConfig 实例
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

        # 子模块
        self._lifecycle = ChannelLifecycle(registry, config)
        self._loop_mgr = ProcessorLoopManager(session_manager)
        self._outbound = OutboundDispatcher(registry, session_manager)
        self._cmd_handler = GatewayCommandHandler(
            session_manager, self._lifecycle, runner=runner
        )

        # 并发守护：per-session 锁 + 积压队列（防止两条消息并发写 session.messages）
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._pending_msgs: dict[str, list[InboundMessage]] = {}

        # 把 dispatch_inbound 注入 lifecycle，channel 收到消息时回调
        self._lifecycle.set_inbound_handler(self.dispatch_inbound)

        logger.info(
            "ChannelGateway initialized | registry_size={}",
            len(registry),
        )

    # ─── 生命周期委托 ────────────────────────────────────────────────────────────

    async def start_channel(
        self,
        channel_id: str,
        account_id: str,
        config: dict,
    ) -> ChannelAccountSnapshot:
        """启动一个 channel 账户。"""
        return await self._lifecycle.start_channel(channel_id, account_id, config)

    async def stop_channel(self, channel_id: str, account_id: str) -> None:
        """停止一个 channel 账户。"""
        await self._lifecycle.stop_channel(channel_id, account_id)

    async def get_status(self, channel_id: str, account_id: str) -> ChannelAccountSnapshot:
        """查询 channel 账户状态。"""
        return await self._lifecycle.get_status(channel_id, account_id)

    def list_running(self) -> list[dict]:
        """列出所有正在运行的 channel 账户。"""
        return self._lifecycle.list_running()

    async def auto_discover_and_start(self) -> dict:
        """自动扫描适配器并启动 auto_start channel。"""
        return await self._lifecycle.auto_discover_and_start()

    # ─── 出站委托 ────────────────────────────────────────────────────────────────

    async def dispatch_outbound(
        self,
        session_id: str,
        text: str,
        media_urls: Optional[list[str]] = None,
    ) -> dict:
        """手动发送出站消息（API 调用 / 后台推送）。"""
        return await self._outbound.dispatch(session_id, text, media_urls)

    # ─── 清理委托 ────────────────────────────────────────────────────────────────

    async def cleanup_session(self, session_id: str) -> None:
        """清理指定 session 的 EventBus 驱动循环。"""
        await self._loop_mgr.cleanup(session_id)

    async def shutdown(self) -> None:
        """关闭所有 channel 并清理所有 processor 循环。"""
        logger.info("ChannelGateway shutting down...")
        await self._loop_mgr.shutdown()
        await self._lifecycle.shutdown()
        logger.info("ChannelGateway shutdown complete")

    # ─── 入站消息路由 ────────────────────────────────────────────────────────────

    async def dispatch_inbound(self, msg: InboundMessage) -> None:
        """
        所有 channel 适配器的入站消息统一入口。

        处理流程：
          1. Gateway 层命令拦截（/stop /new /reset /status /help）
             → 直接回复用户，不走 Agent 路径
          2. 并发守护：同一 session 同时只允许一个 Agent 运行
             → 新消息到达时 lock 已占则入 pending 队列
          3. 持锁运行 Agent，完成后消费积压队列
        """
        assert msg.channel_id, "InboundMessage.channel_id is required"
        assert msg.account_id, "InboundMessage.account_id is required"
        assert msg.sender_id, "InboundMessage.sender_id is required"

        session_key = self._resolve_session_key(msg)
        text = (msg.text or "").strip()

        # ── Step 1：Gateway 层命令拦截 ──────────────────────────────────────────
        if self._cmd_handler.is_gateway_command(text):
            result = await self._cmd_handler.handle(text, session_key)
            if result.handled:
                if result.reply:
                    await self._send_command_reply(session_key, msg, result.reply)
                return

        # ── Step 2：并发守护 ────────────────────────────────────────────────────
        lock = self._session_locks.setdefault(session_key, asyncio.Lock())
        if lock.locked():
            # Agent 正在运行：入队等待，不起新 Agent（避免并发写 session.messages）
            self._pending_msgs.setdefault(session_key, []).append(msg)
            logger.info(
                "Message queued (agent running) | session={} channel={}",
                session_key[:8], msg.channel_id,
            )
            return

        # ── Step 3：持锁异步运行（锁的生命周期 = 整个 Agent 运行 + 积压消费）───
        asyncio.create_task(self._locked_run(session_key, msg))

    async def _locked_run(self, session_key: str, first_msg: InboundMessage) -> None:
        """
        持 session 锁运行 Agent，完成后消费积压队列。

        锁的持有时间覆盖整个 Agent 运行期间，确保同一 session 的消息串行处理。
        """
        lock = self._session_locks[session_key]
        async with lock:
            await self._run_one(session_key, first_msg)
            # 消费积压队列（当前 Agent 运行期间入队的消息）
            while self._pending_msgs.get(session_key):
                next_msg = self._pending_msgs[session_key].pop(0)
                await self._run_one(session_key, next_msg)

    async def _run_one(self, session_key: str, msg: InboundMessage) -> None:
        """
        执行单次 Agent 运行：Session 查找/创建 → OutputTarget 组装 → Agent 启动。

        注意：此方法在 lock 持有期间被 await，Agent 运行是 await 不是 create_task。
        """
        logger.debug(
            "Dispatch inbound | channel={} sender={} session_key={}",
            msg.channel_id, msg.sender_id, session_key,
        )

        session = self.session_manager.get(session_key)
        if session is None:
            session = self.session_manager.create(session_key)
            logger.info(
                "Session auto-created for inbound | key={} channel={}",
                session_key, msg.channel_id,
            )

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

        target = self._build_output_target(msg, session)
        session.output_targets = [target]
        session.default_output_targets = [target]

        await target.processor.on_turn_start()
        await self._loop_mgr.ensure(session.id)

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

        try:
            await self.runner.run(session, msg.text, bus_emitter)
        except Exception as e:
            logger.error(
                "Agent run failed | session={} err={}",
                session.id[:8], e,
            )
            await self._outbound.send_error_reply(session.id, str(e))

    async def _send_command_reply(
        self, session_key: str, msg: InboundMessage, reply: str
    ) -> None:
        """将 Gateway 命令的回复发送给用户。"""
        adapter = self.registry.get_adapter(msg.channel_id)
        if adapter is None:
            logger.warning(
                "_send_command_reply: adapter not found | channel={}",
                msg.channel_id,
            )
            return
        to = msg.sender_id if msg.chat_type == "direct" else (msg.thread_id or msg.sender_id)
        try:
            await adapter.send_text(msg.account_id, to, reply, reply_to_id=msg.message_id)
        except Exception as e:
            logger.error(
                "_send_command_reply failed | channel={} err={}",
                msg.channel_id, e,
            )

    # ─── Push 路由注册（定时任务 / 后台 Agent 主动推送）────────────────────────

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
        预注册推送路由（供 Cron / Background Agent 使用）。

        当没有用户消息触发时，仍可通过本方法绑定"发给谁"的路由。
        """
        session = self.session_manager.get(session_id)
        if session is None:
            logger.warning(
                "register_push_route: session not found | id={} channel={}",
                session_id[:8], channel_id,
            )
            return

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
        session.default_output_targets = [target]
        await self._loop_mgr.ensure(session_id)

        logger.info(
            "Push route registered | session={} channel={} to={}",
            session_id[:8], channel_id, to,
        )

    # ─── 内部辅助 ────────────────────────────────────────────────────────────────

    def _build_output_target(self, msg: InboundMessage, session) -> OutputTarget:
        """根据入站消息构建 OutputTarget + Processor。"""
        to = msg.sender_id if msg.chat_type == "direct" else (msg.thread_id or msg.sender_id)

        target = OutputTarget(
            channel_id=msg.channel_id,
            account_id=msg.account_id,
            to=to,
            reply_to_id=msg.message_id,
            processor=None,
        )

        adapter = self.registry.get_adapter(msg.channel_id)
        if adapter is not None:
            target.processor = adapter.build_processor(target)
        else:
            from ccserver.channels.processor import Processor as NoOpProcessor
            target.processor = NoOpProcessor()
            logger.warning(
                "_build_output_target: adapter not found, using no-op Processor | channel={}",
                msg.channel_id,
            )

        return target

    def _resolve_session_key(self, msg: InboundMessage) -> str:
        """
        根据入站消息解析 session key。

        - 私聊："{channel_id}:{account_id}:{sender_id}"
        - 群聊："{channel_id}:{account_id}:group:{thread_id}"
        """
        if msg.chat_type == "direct":
            return f"{msg.channel_id}:{msg.account_id}:{msg.sender_id}"
        else:
            thread_part = msg.thread_id or msg.sender_id
            return f"{msg.channel_id}:{msg.account_id}:group:{thread_part}"
