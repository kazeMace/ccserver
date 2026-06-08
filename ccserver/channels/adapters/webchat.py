"""
channels/adapters/webchat — 内置 WebChat Channel 适配器。

将现有的 SSEEmitter / WSEmitter 包装为标准的 BaseChannelAdapter。
这样 WebChat 和 Discord/飞书/钉钉在架构上完全对等。

职责
────
  - 入站：接收 server.py HTTP/WS 路由层传入的用户消息
  - 出站：通过 SSEEmitter / WSEmitter 将回复推送给 Web 客户端
  - 生命周期：SSE/WS 连接建立 → 适配器上线；连接断开 → 适配器离线

WebChat 的特殊性
───────────────
  WebChat 不是一个"外部平台"，而是 ccserver 的内置客户端。
  因此：
    - start() / stop() 不需要主动连接外部服务
    - send_text() 实际上是向已连接的 SSE/WS 客户端推送消息
    - 入站消息由 server.py 的 HTTP 路由层调用 receive_message() 注入
"""

import asyncio
import time
import uuid
from typing import Optional, Any

from loguru import logger

from ..base import (
    BaseChannelAdapter,
    ChannelCapabilities,
    ChannelAccountSnapshot,
    InboundMessage,
    OutboundMessage,
)
from ..processor import Processor
from ..output_target import OutputTarget


class WebChatAdapter(BaseChannelAdapter):
    """
    内置 WebChat channel 适配器。

    将 SSEEmitter / WSEmitter 包装为标准 BaseChannelAdapter。

    与 OpenClaw 的内置 WebChat channel 对应。

    Attributes:
        channel_id: "webchat"
        aliases: ["web", "browser", "http"]
        meta: 支持 direct，无媒体，Markdown 渲染，单条无限制
    """

    channel_id = "webchat"
    aliases = ["web", "browser", "http"]
    meta = ChannelCapabilities(
        chat_types=["direct"],
        supports_media=False,
        supports_reactions=False,
        supports_reply=False,
        supports_edit=False,
        supports_delete=False,
        markdown_capable=True,
        max_text_length=100_000,  # Web 端无限制，设大一些
    )

    def __init__(self):
        super().__init__()

        # session_id -> emitter 实例（SSEEmitter 或 WSEmitter）
        self._emitters: dict[str, Any] = {}

        # session_id -> 客户端元信息
        self._clients: dict[str, dict] = {}

        # account_id -> 在线客户端数
        self._online_counts: dict[str, int] = {}

        # 等待回复的协程字典（用于 WSEmitter 的 AskUserQuestion 机制）
        self._pending_replies: dict[str, asyncio.Future] = {}

        logger.debug("WebChatAdapter initialized")

    def build_processor(self, target: "OutputTarget") -> "Processor":
        """
        创建 WebChat 专用 Processor。

        WebChatProcessor 只处理 ask_user 和 permission_request 的交互回路。
        token / done 事件仍由 SSEEmitter 的 EventBus 订阅独立推送（不重复处理）。

        Args:
            target: OutputTarget 实例，target.to 即为 session_id（WebChat 的路由 key）。
        """
        return WebChatProcessor(adapter=self, session_id=target.to)

    # ── 注册/注销 emitter ────────────────────────────────────────────────────

    def register_emitter(
        self,
        session_id: str,
        emitter: Any,
        account_id: str = "default",
        client_info: Optional[dict] = None,
    ) -> None:
        """
        注册一个 SSE/WS emitter（由 server.py HTTP 路由层调用）。

        当用户建立 SSE 连接或 WS 连接时，server.py 调用此方法
        将 emitter 注册到适配器，之后入站消息可以通过 receive_message() 注入，
        出站消息可以通过 send_text() 推送。

        Args:
            session_id: Session ID
            emitter:    SSEEmitter 或 WSEmitter 实例
            account_id: 账户标识（默认 "default"）
            client_info: 客户端信息（ip、user_agent 等，可选）
        """
        # 先注销旧的
        self._unregister_session(session_id)

        self._emitters[session_id] = emitter
        self._clients[session_id] = {
            "account_id": account_id,
            "client_info": client_info or {},
            "connected_at": time.time(),
            "last_active": time.time(),
        }

        self._online_counts[account_id] = self._online_counts.get(account_id, 0) + 1

        logger.info(
            "WebChat emitter registered | session_id={} account={} "
            "online_count={}",
            session_id[:8], account_id, self._online_counts[account_id],
        )

    def _unregister_session(self, session_id: str) -> None:
        """内部方法：注销一个 session 的 emitter。"""
        old_emitter = self._emitters.pop(session_id, None)
        client = self._clients.pop(session_id, None)

        if old_emitter is not None and client is not None:
            account_id = client["account_id"]
            self._online_counts[account_id] = max(0, self._online_counts.get(account_id, 0) - 1)
            logger.info(
                "WebChat emitter unregistered | session_id={} account={} "
                "online_count={}",
                session_id[:8], account_id, self._online_counts.get(account_id, 0),
            )

    def unregister_session(self, session_id: str) -> None:
        """
        注销一个 session 的 emitter（由 server.py HTTP 路由层调用）。

        当 SSE 连接断开或 WS 关闭时，server.py 调用此方法清理资源。

        Args:
            session_id: Session ID
        """
        self._unregister_session(session_id)

    # ── 入站消息接收 ──────────────────────────────────────────────────────────

    async def receive_message(
        self,
        session_id: str,
        text: str,
        sender_id: str = "user",
        sender_name: str = "User",
        metadata: Optional[dict] = None,
    ) -> None:
        """
        接收用户消息（由 server.py HTTP/WS 路由层调用）。

        当用户通过 POST /chat 或 WS 发送消息时，server.py 构造为
        统一的 InboundMessage 交给 ChannelGateway 处理。

        Args:
            session_id:  Session ID
            text:        消息文本
            sender_id:   发送者 ID（默认 "user"）
            sender_name:  发送者显示名（默认 "User"）
            metadata:     额外元信息（会合并到 InboundMessage._meta）
        """
        # 更新最后活跃时间
        if session_id in self._clients:
            self._clients[session_id]["last_active"] = time.time()

        if self._inbound_handler is None:
            logger.warning(
                "receive_message: no inbound handler | session_id={}",
                session_id[:8],
            )
            return

        client = self._clients.get(session_id, {})

        # 构造 InboundMessage
        inbound = InboundMessage(
            channel_id=self.channel_id,
            account_id=client.get("account_id", "default"),
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            chat_type="direct",
            message_id=str(uuid.uuid4()),
            timestamp=time.time(),
            raw_payload=metadata,
        )

        await self._dispatch_inbound(inbound)

    # ── 生命周期管理 ──────────────────────────────────────────────────────────

    async def start(self, account_id: str, config: dict) -> ChannelAccountSnapshot:
        """
        WebChat 无需主动连接外部服务。

        emitter 注册即视为在线。start() 仅初始化账户状态。

        Args:
            account_id: 账户标识
            config:     配置字典（WebChat 不使用）

        Returns:
            账户状态快照
        """
        logger.info("WebChat channel start | account={}", account_id)

        return ChannelAccountSnapshot(
            account_id=account_id,
            enabled=True,
            configured=True,
            linked=True,
            running=True,
            connected=True,
            last_connected_at=_iso_now(),
        )

    async def stop(self, account_id: str) -> None:
        """
        停止 channel。

        关闭所有属于该账户的 emitter 连接。

        Args:
            account_id: 账户标识
        """
        logger.info("WebChat channel stop | account={}", account_id)

        # 找到并关闭所有属于该账户的 session
        to_close = [
            sid for sid, c in self._clients.items()
            if c["account_id"] == account_id
        ]

        for session_id in to_close:
            self._unregister_session(session_id)

    async def get_status(self, account_id: str) -> ChannelAccountSnapshot:
        """
        查询账户状态。

        Returns:
            账户状态快照，包含在线客户端数
        """
        online = self._online_counts.get(account_id, 0)

        return ChannelAccountSnapshot(
            account_id=account_id,
            enabled=True,
            configured=True,
            linked=True,
            running=True,
            connected=online > 0,
            last_connected_at=_iso_now(),
            reconnect_attempts=0,
        )

    # ── 出站消息发送 ──────────────────────────────────────────────────────────

    async def send_text(
        self,
        account_id: str,
        to: str,
        text: str,
        reply_to_id: Optional[str] = None,
    ) -> dict:
        """
        发送文本到 WebChat 客户端。

        Args:
            account_id:  发送方账户标识（忽略，to 即 session_id）
            to:          目标 session_id（WebChatAdapter 的 to 就是 session_id）
            text:        消息文本
            reply_to_id: 回复消息 ID（WebChat 暂不支持）

        Returns:
            发送结果字典
        """
        emitter = self._emitters.get(to)

        if emitter is None:
            logger.warning(
                "send_text: no active WebChat connection | to={}",
                to[:8],
            )
            return {
                "success": False,
                "error": f"No active connection for session {to[:8]}",
            }

        try:
            await emitter.emit({"type": "message", "content": text})
            return {
                "success": True,
                "platform_message_id": str(uuid.uuid4()),
            }
        except Exception as e:
            logger.error(
                "send_text failed | to={} err={}",
                to[:8], e,
            )
            return {"success": False, "error": str(e)}

    async def send_message(
        self,
        account_id: str,
        to: str,
        msg: OutboundMessage,
    ) -> dict:
        """
        统一出站入口。

        WebChat 暂不支持媒体，所有 media_urls 被忽略。

        Args:
            account_id: 发送方账户标识
            to:         目标 session_id
            msg:        出站消息对象

        Returns:
            发送结果字典
        """
        if not msg.text:
            return {"success": True, "results": [], "success_count": 0, "total_count": 0}

        emitter = self._emitters.get(to)

        if emitter is None:
            return {
                "success": False,
                "error": f"No active connection for session {to[:8]}",
                "results": [],
                "success_count": 0,
                "total_count": 1,
            }

        try:
            # 构造 SSE/WS 事件
            # 注意：这里复用 BaseEmitter 的 fmt_* 方法生成标准格式
            from ccserver.emitters.base import BaseEmitter

            class _FmtEmitter(BaseEmitter):
                async def emit(self, event: dict) -> None:
                    pass

            fmt = _FmtEmitter()

            # 发送 done 事件（包含最终内容）
            done_event = fmt.fmt_done(msg.text)
            await emitter.emit(done_event)

            return {
                "success": True,
                "results": [{"success": True}],
                "success_count": 1,
                "total_count": 1,
            }
        except Exception as e:
            logger.error(
                "send_message failed | to={} err={}",
                to[:8], e,
            )
            return {
                "success": False,
                "error": str(e),
                "results": [],
                "success_count": 0,
                "total_count": 1,
            }

    # ── AskUserQuestion 支持 ─────────────────────────────────────────────────

    async def emit_ask_user(self, session_id: str, questions: list) -> str:
        """
        向 WebChat 客户端推送问题并等待回答。

        用于 WSEmitter 的 emit_ask_user 机制。

        Args:
            session_id: Session ID
            questions:  问题列表

        Returns:
            用户回答字符串
        """
        emitter = self._emitters.get(session_id)

        if emitter is None or not hasattr(emitter, "emit_ask_user"):
            logger.warning(
                "emit_ask_user: emitter not found or not supported | session_id={}",
                session_id[:8],
            )
            return ""

        try:
            return await emitter.emit_ask_user(questions)
        except Exception as e:
            logger.error(
                "emit_ask_user failed | session_id={} err={}",
                session_id[:8], e,
            )
            return ""

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def list_sessions(self) -> list[dict]:
        """
        列出所有活跃的 WebChat 会话。

        Returns:
            活跃会话列表
        """
        return [
            {
                "session_id": sid,
                "account_id": c["account_id"],
                "connected_at": c["connected_at"],
                "last_active": c["last_active"],
                "client_info": c["client_info"],
            }
            for sid, c in self._clients.items()
        ]

    def get_session_count(self, account_id: str) -> int:
        """返回指定账户的在线会话数。"""
        return sum(
            1 for c in self._clients.values()
            if c["account_id"] == account_id
        )


# ── WebChatProcessor ─────────────────────────────────────────────────────────


class WebChatProcessor(Processor):
    """
    WebChat 专用 Processor。

    职责：处理 Gateway 驱动流中的 ask_user 和 permission_request 交互回路。
    token / done 事件由 SSEEmitter 的 EventBus 订阅独立处理，本 Processor 不重复推送。

    ask_user 实现方式：
      1. 从 WebChatAdapter._emitters[session_id] 获取 SSEEmitter。
      2. 向 SSEEmitter 推送 ask_user SSE 事件（放入 SSE 队列）。
      3. 在 SSEEmitter 上设置 _answer_cb，等待 HTTP POST /answer 到来时触发。
      4. answer_cb(text) 将答案注入 BusEmitter 的 future，Agent 继续执行。

    permission_request 实现方式与 ask_user 相同，使用 _grant_cb。

    Args:
        adapter:    WebChatAdapter 实例（持有 SSEEmitter 引用）。
        session_id: 当前 WebChat 会话的 session_id（= OutputTarget.to）。
    """

    def __init__(self, adapter: "WebChatAdapter", session_id: str):
        self._adapter = adapter
        self._session_id = session_id

    async def on_ask_user(self, questions: list, answer_cb) -> None:
        """
        向 WebChat 客户端推送 ask_user 事件，注册 answer_cb 等待 HTTP /answer 回调。

        Args:
            questions:  问题列表。
            answer_cb:  answer_cb(text) → 将用户回答注入 BusEmitter future。
        """
        emitter = self._adapter._emitters.get(self._session_id)
        if emitter is None:
            # 无 SSE 连接，直接返回空答案
            logger.warning(
                "WebChatProcessor.on_ask_user: no SSE emitter | session={}",
                self._session_id[:8],
            )
            answer_cb("")
            return

        # 向 SSE 队列推送 ask_user 事件
        from ccserver.emitters.base import BaseEmitter
        fmt = BaseEmitter.__new__(BaseEmitter)
        await emitter.emit(fmt.fmt_ask_user(questions))

        # 在 SSEEmitter 上设置回调，HTTP /answer 到来时触发
        emitter._answer_cb = answer_cb

    async def on_permission_request(self, tool_name: str, tool_input: dict, grant_cb) -> None:
        """
        向 WebChat 客户端推送 permission_request 事件，注册 grant_cb 等待 HTTP /permission 回调。

        Args:
            tool_name:  工具名称。
            tool_input: 工具输入参数。
            grant_cb:   grant_cb(True/False) → 将审批结果注入 BusEmitter future。
        """
        emitter = self._adapter._emitters.get(self._session_id)
        if emitter is None:
            logger.warning(
                "WebChatProcessor.on_permission_request: no SSE emitter | session={}",
                self._session_id[:8],
            )
            grant_cb(False)
            return

        from ccserver.emitters.base import BaseEmitter
        fmt = BaseEmitter.__new__(BaseEmitter)
        await emitter.emit(fmt.fmt_permission_request(tool_name, tool_input))

        # 在 SSEEmitter 上设置回调
        emitter._grant_cb = grant_cb


# ── 辅助函数 ─────────────────────────────────────────────────────────────────


def _iso_now() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
