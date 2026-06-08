"""
channels/base — Channel 系统的核心抽象层。

与 OpenClaw 的对应关系
──────────────────────
BaseChannelAdapter      → OpenClaw ChannelOutboundAdapter + ChannelMessageActionAdapter
ChannelCapabilities     → OpenClaw ChannelCapabilities
ChannelAccountSnapshot  → OpenClaw ChannelAccountSnapshot
InboundMessage          → OpenClaw MsgContext（入站侧简化）
OutboundMessage         → OpenClaw ReplyPayload（出站侧简化）

每个平台适配器只需继承 BaseChannelAdapter，实现几个抽象方法即可接入 ccserver。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Awaitable, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from ccserver.channels.output_target import OutputTarget
    from ccserver.channels.processor import Processor


# ── ChatType 枚举 ─────────────────────────────────────────────────────────────


class ChatType(str, Enum):
    """
    聊天类型，与 OpenClaw 的 ChatType 对齐。

    Values:
        direct : 私聊 / DM（一对一）
        group  : 群聊（多人在同一个对话中）
        channel: 频道（类似 Discord channel、Slack channel）
    """
    DIRECT = "direct"
    GROUP = "group"
    CHANNEL = "channel"


# ── ChannelCapabilities ───────────────────────────────────────────────────────


@dataclass
class ChannelCapabilities:
    """
    Channel 的静态能力声明。

    每个适配器在类级别声明自己的能力，gateway 据此决定：
      - 是否支持发送媒体
      - 是否支持 Markdown 渲染
      - 单条消息最大长度（用于自动分块）
      - 等等

    与 OpenClaw 的 ChannelCapabilities 对齐。

    Attributes:
        chat_types:          支持的聊天类型列表，如 ["direct", "group"]
        supports_media:      是否支持发送图片/文件/语音等媒体
        supports_reactions:  是否支持表情反应（emoji reactions）
        supports_reply:      是否支持回复/线程（reply-to / thread）
        supports_edit:       是否支持编辑已发送的消息
        supports_delete:     是否支持删除已发送的消息
        markdown_capable:    是否支持 Markdown 渲染（否则需转纯文本）
        max_text_length:     单条消息的最大字符数，超出需分块发送
    """
    chat_types: list[str] = field(default_factory=lambda: ["direct"])
    supports_media: bool = False
    supports_reactions: bool = False
    supports_reply: bool = False
    supports_edit: bool = False
    supports_delete: bool = False
    markdown_capable: bool = False
    max_text_length: int = 4000


# ── ChannelAccountSnapshot ────────────────────────────────────────────────────


@dataclass
class ChannelAccountSnapshot:
    """
    Channel 账户的运行时状态快照。

    与 OpenClaw 的 ChannelAccountSnapshot 对齐。
    用于 health monitor、UI 状态展示和诊断。

    Attributes:
        account_id:           账户标识（如 bot 用户名）
        enabled:              是否已启用
        configured:           配置是否完整（token 等是否已填写）
        linked:               是否已完成 OAuth / 授权绑定
        running:              连接是否正在运行（WebSocket/Stream 是否在线）
        connected:            当前是否已连接（实时状态）
        last_connected_at:    上次成功连接的时间戳（ISO 格式或 None）
        last_error:           上次错误信息（None 表示无错误）
        reconnect_attempts:   重连尝试次数
    """
    account_id: str
    enabled: bool = False
    configured: bool = False
    linked: bool = False
    running: bool = False
    connected: bool = False
    last_connected_at: Optional[str] = None
    last_error: Optional[str] = None
    reconnect_attempts: int = 0

    def to_dict(self) -> dict:
        """返回字典形式，用于 API 序列化。"""
        return {
            "account_id": self.account_id,
            "enabled": self.enabled,
            "configured": self.configured,
            "linked": self.linked,
            "running": self.running,
            "connected": self.connected,
            "last_connected_at": self.last_connected_at,
            "last_error": self.last_error,
            "reconnect_attempts": self.reconnect_attempts,
        }


# ── InboundMessage ────────────────────────────────────────────────────────────


@dataclass
class InboundMessage:
    """
    统一入站消息格式。

    所有 channel 适配器收到平台消息后，必须转换为这个格式
    再交给 ChannelGateway 处理。

    与 OpenClaw 的 MsgContext 对齐（简化版）。

    Attributes:
        channel_id:    消息来源 channel 的 ID（如 "discord", "webchat"）
        account_id:    接收消息的账户标识（多账户场景下区分不同 bot）
        sender_id:     发送者在平台上的唯一 ID
        sender_name:   发送者的显示名称（人类可读）
        text:          消息文本内容（纯文本，适配器负责提取和清洗）
        chat_type:     聊天类型："direct" | "group" | "channel"
        thread_id:     线程/群组 ID（群聊时必填，私聊时可空）
        message_id:    平台消息 ID（用于回复、编辑、删除）
        media_urls:    附件/媒体 URL 列表（图片、文件等）
        timestamp:     消息发送时间戳（Unix timestamp，秒）
        raw_payload:   原始平台消息（可选，用于调试和特殊处理）
    """
    channel_id: str
    account_id: str
    sender_id: str
    sender_name: str = ""
    text: str = ""
    chat_type: str = "direct"
    thread_id: Optional[str] = None
    message_id: Optional[str] = None
    media_urls: list[str] = field(default_factory=list)
    timestamp: float = 0.0
    raw_payload: Optional[dict] = None

    def __post_init__(self):
        # 确保 chat_type 是合法值
        if self.chat_type not in ("direct", "group", "channel"):
            logger.warning(
                "InboundMessage: invalid chat_type '{}', defaulting to 'direct' | "
                "channel_id={} sender_id={}",
                self.chat_type, self.channel_id, self.sender_id,
            )
            self.chat_type = "direct"


# ── OutboundMessage ───────────────────────────────────────────────────────────


@dataclass
class OutboundMessage:
    """
    统一出站消息格式。

    Agent 回复通过这个格式交给 ChannelGateway，
    再由具体适配器转换为平台特定格式发送。

    与 OpenClaw 的 ReplyPayload 对齐（简化版）。

    Attributes:
        text:        回复文本内容
        media_urls:  要发送的媒体 URL 列表（图片、文件等）
        reply_to_id: 回复哪条消息的 ID（平台消息 ID）
        thread_id:   发送到哪个线程/群组
    """
    text: str = ""
    media_urls: list[str] = field(default_factory=list)
    reply_to_id: Optional[str] = None
    thread_id: Optional[str] = None


# ── BaseChannelAdapter ────────────────────────────────────────────────────────


class BaseChannelAdapter(ABC):
    """
    所有 channel 适配器的抽象基类。

    与 OpenClaw 的 ChannelOutboundAdapter + ChannelMessageActionAdapter 对齐。
    每个外部平台（Discord、Telegram、Slack、飞书、钉钉、QQ 等）需要实现这个类。

    子类必须定义的类属性（class attributes）：
        channel_id:  str  — 唯一标识，如 "discord"
        aliases:     list — 别名列表，如 ["dc"]
        meta:        ChannelCapabilities — 静态能力声明

    子类必须实现的抽象方法：
        start()  — 启动 channel 连接
        stop()   — 停止 channel 连接
        send_text() — 发送纯文本消息

    可选覆盖的方法：
        send_media()    — 发送媒体（默认抛出 NotImplementedError）
        send_message()  — 统一出站入口（默认组合 text + media）
        get_status()    — 查询状态（默认返回基础 snapshot）

    使用示例
    ────────
    class DiscordAdapter(BaseChannelAdapter):
        channel_id = "discord"
        aliases = ["dc"]
        meta = ChannelCapabilities(
            chat_types=["direct", "group"],
            supports_media=True,
            markdown_capable=True,
            max_text_length=2000,
        )

        async def start(self, account_id: str, config: dict) -> ChannelAccountSnapshot:
            # 连接 Discord Gateway...
            return ChannelAccountSnapshot(account_id=account_id, running=True)

        async def send_text(self, account_id, to, text, reply_to_id=None):
            # 调用 Discord API 发送消息...
            return {"platform_message_id": "123"}
    """

    # ── 类属性（子类必须定义）─────────────────────────────────────────────────

    channel_id: str = ""
    aliases: list[str] = field(default_factory=list)
    meta: ChannelCapabilities = field(default_factory=ChannelCapabilities)

    # ── 构造函数 ──────────────────────────────────────────────────────────────

    def __init__(self):
        # 入站消息回调，由 ChannelGateway 通过 set_inbound_handler 注册
        self._inbound_handler: Optional[
            Callable[[InboundMessage], Awaitable[None]]
        ] = None

        # 断言：子类必须定义 channel_id
        assert self.channel_id, (
            f"{self.__class__.__name__} must define class attribute 'channel_id'"
        )
        # 断言：子类必须定义 meta
        assert isinstance(self.meta, ChannelCapabilities), (
            f"{self.__class__.__name__}.meta must be a ChannelCapabilities instance"
        )

        logger.debug(
            "Channel adapter initialized | channel_id={} aliases={}",
            self.channel_id, self.aliases,
        )

    # ── 入站消息回调注册 ──────────────────────────────────────────────────────

    def set_inbound_handler(
        self,
        handler: Callable[[InboundMessage], Awaitable[None]],
    ) -> None:
        """
        注册入站消息回调。

        由 ChannelGateway 在 start_channel() 时调用。
        适配器收到平台消息后，必须调用这个 handler 将消息送入系统。

        Args:
            handler: 异步回调函数，接收 InboundMessage。
        """
        assert handler is not None, "inbound handler cannot be None"
        self._inbound_handler = handler
        logger.debug(
            "Inbound handler registered | channel_id={}",
            self.channel_id,
        )

    async def _dispatch_inbound(self, msg: InboundMessage) -> None:
        """
        内部辅助方法：将入站消息分发给 handler。

        子类在收到平台消息后应调用此方法，而非直接调用 self._inbound_handler。
        该方法会填充 channel_id 和 account_id（如果缺失），并做日志记录。

        Args:
            msg: 入站消息。如果 channel_id 或 account_id 为空，会自动填充。
        """
        assert self._inbound_handler is not None, (
            f"{self.__class__.__name__}: inbound handler not set. "
            "Did you forget to call set_inbound_handler()?"
        )

        # 自动填充缺失字段
        if not msg.channel_id:
            msg.channel_id = self.channel_id

        logger.info(
            "Inbound message | channel={} account={} sender={} chat_type={} "
            "text_len={} thread_id={}",
            msg.channel_id, msg.account_id, msg.sender_id,
            msg.chat_type, len(msg.text), msg.thread_id,
        )

        try:
            await self._inbound_handler(msg)
        except Exception as e:
            logger.error(
                "Inbound handler failed | channel={} sender={} err={}",
                msg.channel_id, msg.sender_id, e,
            )
            raise

    # ── 生命周期管理（抽象方法）───────────────────────────────────────────────

    @abstractmethod
    async def start(self, account_id: str, config: dict) -> ChannelAccountSnapshot:
        """
        启动 channel 连接。

        Args:
            account_id: 账户标识（如 Discord bot 的用户名、飞书 tenant key）。
            config:     配置字典，内容因平台而异：
                          - Discord: {"token": "..."}
                          - 飞书:    {"app_id": "...", "app_secret": "..."}
                          - 钉钉:    {"client_id": "...", "client_secret": "..."}

        Returns:
            启动后的账户状态快照。

        Raises:
            ValueError:     配置缺失或格式错误
            RuntimeError:   连接失败
        """
        ...

    @abstractmethod
    async def stop(self, account_id: str) -> None:
        """
        停止 channel 连接，释放资源（WebSocket、HTTP client、轮询任务等）。

        Args:
            account_id: 要停止的账户标识。
        """
        ...

    # ── 出站消息发送（抽象 + 可选方法）────────────────────────────────────────

    @abstractmethod
    async def send_text(
        self,
        account_id: str,
        to: str,
        text: str,
        reply_to_id: Optional[str] = None,
    ) -> dict:
        """
        发送纯文本消息。

        Args:
            account_id:  发送方账户标识
            to:            目标用户/群组/频道的平台 ID
            text:          消息文本（已清洗，无 Markdown 如果平台不支持）
            reply_to_id:   要回复的消息 ID（平台消息 ID），可选

        Returns:
            发送结果字典，建议包含：
              - platform_message_id: str — 平台返回的消息 ID
              - success: bool
              - error: str | None
        """
        ...

    async def send_media(
        self,
        account_id: str,
        to: str,
        media_url: str,
        caption: Optional[str] = None,
    ) -> dict:
        """
        发送媒体消息（图片、文件、语音等）。

        默认实现：抛出 NotImplementedError。
        子类如果声明 meta.supports_media=True，必须覆盖此方法。

        Args:
            account_id: 发送方账户标识
            to:           目标用户/群组/频道的平台 ID
            media_url:    媒体文件的 URL（http/https）或本地路径
            caption:      媒体附带的文字说明

        Returns:
            发送结果字典，格式同 send_text。

        Raises:
            NotImplementedError: 如果子类未覆盖此方法。
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support media sending. "
            "Override send_media() or set meta.supports_media=False."
        )

    async def send_message(
        self,
        account_id: str,
        to: str,
        msg: OutboundMessage,
    ) -> dict:
        """
        统一出站入口。默认行为：
          1. 如果有 media_urls 且平台支持媒体，先发送媒体
          2. 如果有 text，发送文本

        子类可覆盖此方法以实现更复杂的逻辑：
          - 合并 text + media 为一张卡片（飞书/钉钉）
          - 分块发送超长文本
          - 流式编辑同一条消息（Discord、Telegram）

        Args:
            account_id: 发送方账户标识
            to:           目标用户/群组/频道的平台 ID
            msg:          出站消息对象

        Returns:
            合并后的发送结果字典。
        """
        results = []

        # 发送媒体
        if msg.media_urls and self.meta.supports_media:
            for url in msg.media_urls:
                try:
                    r = await self.send_media(
                        account_id, to, url, caption=msg.text if len(msg.media_urls) == 1 else None,
                    )
                    results.append(r)
                except Exception as e:
                    logger.error(
                        "Media send failed | channel={} account={} to={} url={} err={}",
                        self.channel_id, account_id, to, url, e,
                    )
                    results.append({"success": False, "error": str(e)})

        # 发送文本
        if msg.text:
            # 如果平台不支持 Markdown，需要清洗
            text = msg.text
            if not self.meta.markdown_capable:
                text = self._strip_markdown(text)

            # 分块处理：如果文本超过平台限制，拆分成多条消息
            chunks = self._chunk_text(text, self.meta.max_text_length)
            for chunk in chunks:
                try:
                    r = await self.send_text(
                        account_id, to, chunk,
                        reply_to_id=msg.reply_to_id if chunk == chunks[0] else None,
                    )
                    results.append(r)
                except Exception as e:
                    logger.error(
                        "Text send failed | channel={} account={} to={} err={}",
                        self.channel_id, account_id, to, e,
                    )
                    results.append({"success": False, "error": str(e)})

        # 统计结果
        success_count = sum(1 for r in results if r.get("success", True))
        return {
            "success": success_count == len(results) and len(results) > 0,
            "results": results,
            "success_count": success_count,
            "total_count": len(results),
        }

    async def get_status(self, account_id: str) -> ChannelAccountSnapshot:
        """
        查询当前账户状态。

        默认实现返回一个基础的 snapshot（running=False）。
        子类应覆盖此方法以返回真实的连接状态。

        Args:
            account_id: 账户标识

        Returns:
            账户状态快照
        """
        return ChannelAccountSnapshot(account_id=account_id)

    # ── 新出站架构：Processor 工厂方法 ──────────────────────────────────────────

    def build_processor(self, target: "OutputTarget") -> "Processor":
        """
        为本 channel 创建出站 Processor 实例。

        默认实现：返回 PassthroughProcessor，收到 done 事件直接调用 send_text()。
        子类（如 WebChatAdapter）可覆盖以返回自定义 Processor。

        Args:
            target: 已创建好的 OutputTarget 实例（processor 字段尚未设置）。

        Returns:
            Processor 实例。
        """
        from ccserver.channels.processor import PassthroughProcessor
        return PassthroughProcessor(adapter=self, target=target)

    # handle_outbound_event / _get_session_route 已在新出站架构中移除。
    # 出站回复通过 OutputTarget + Processor 机制驱动，无需 OutboundBus 集成层。

    # ── 内部工具方法 ──────────────────────────────────────────────────────────

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """
        将 Markdown 格式转换为纯文本。

        简单实现：移除常见 Markdown 标记（粗体、斜体、代码块、链接等）。
        子类可覆盖以适配平台特定的格式需求。

        Args:
            text: 带 Markdown 的文本

        Returns:
            纯文本
        """
        import re

        # 移除代码块
        text = re.sub(r"```[\s\S]*?```", "[code block]", text)
        # 移除行内代码
        text = re.sub(r"`([^`]+)`", r"\1", text)
        # 移除链接，保留文本
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        # 移除粗体/斜体标记
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        text = re.sub(r"\*([^*]+)\*", r"\1", text)
        text = re.sub(r"__([^_]+)__", r"\1", text)
        text = re.sub(r"_([^_]+)_", r"\1", text)
        # 移除标题标记
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)

        return text.strip()

    @staticmethod
    def _chunk_text(text: str, max_length: int) -> list[str]:
        """
        将长文本按平台限制分块。

        尽量在段落边界或空格处切分，避免切断单词。
        如果单段超过限制，则强制切分。

        Args:
            text:        原始文本
            max_length:  每块最大字符数

        Returns:
            文本块列表
        """
        if len(text) <= max_length:
            return [text]

        chunks = []
        remaining = text

        while remaining:
            if len(remaining) <= max_length:
                chunks.append(remaining)
                break

            # 在 max_length 之前找最后一个换行符或空格
            chunk = remaining[:max_length]

            # 优先在换行处切分
            last_newline = chunk.rfind("\n")
            if last_newline > max_length // 2:
                cut_at = last_newline
            else:
                # 其次在空格处切分
                last_space = chunk.rfind(" ")
                if last_space > max_length // 2:
                    cut_at = last_space
                else:
                    cut_at = max_length  # 强制切分

            chunks.append(remaining[:cut_at])
            remaining = remaining[cut_at:].lstrip()

        return chunks
