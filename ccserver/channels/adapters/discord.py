"""
channels/adapters/discord — Discord Channel 适配器。

与 OpenClaw 的 Discord channel 插件对齐。
基于 discord.py (v2.x) 实现，支持：
  - Bot Token 认证
  - 接收 DM 和 Guild 消息
  - 发送文本回复（支持 reply_to / mention）
  - 发送媒体（图片、文件）
  - 连接状态管理

依赖
────
    pip install discord.py

配置示例 (channels.json)
────────────────────────
    {
        "channels": {
            "discord": {
                "enabled": true,
                "auto_start": true,
                "accounts": {
                    "default": {
                        "token": "YOUR_BOT_TOKEN_HERE",
                        "intents": ["messages", "guilds", "message_content"]
                    }
                }
            }
        }
    }

使用方式
────────
    # 1. 创建 Discord Bot：https://discord.com/developers/applications
    # 2. 获取 Bot Token，填入 channels.json
    # 3. 启动 ccserver，Discord 适配器自动连接
    # 4. 将 Bot 邀请到你的 Discord 服务器

注意事项
────────
  - Discord Bot 必须开启以下 Privileged Gateway Intents：
      * MESSAGE CONTENT INTENT（用于读取消息内容）
  - 单条消息最大长度 2000（免费 Bot）或 4000（Nitro Bot）
  - Discord Markdown 与标准 Markdown 有差异（如 **粗体** 相同，但 spoiler、mention 等是 Discord 特有）
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from ..base import (
    BaseChannelAdapter,
    ChannelCapabilities,
    ChannelAccountSnapshot,
    InboundMessage,
    OutboundMessage,
)

# discord.py 是可选依赖，未安装时给出友好提示
try:
    import discord
    from discord import Intents
    _DISCORD_AVAILABLE = True
except ImportError:
    _DISCORD_AVAILABLE = False
    discord = None  # type: ignore
    Intents = None  # type: ignore


# ── DiscordAdapter ────────────────────────────────────────────────────────────


class DiscordAdapter(BaseChannelAdapter):
    """
    Discord channel 适配器。

    基于 discord.py v2.x 实现，通过 Discord Gateway 接收消息，
    通过 Discord REST API 发送回复。

    与 OpenClaw 的 Discord channel 插件对齐。

    Attributes:
        channel_id: "discord"
        aliases: ["dc"]
        meta: 支持 direct + group，支持媒体、回复、Markdown
    """

    channel_id = "discord"
    aliases = ["dc"]
    meta = ChannelCapabilities(
        chat_types=["direct", "group", "channel"],
        supports_media=True,
        supports_reactions=True,
        supports_reply=True,
        supports_edit=True,
        supports_delete=True,
        markdown_capable=True,
        max_text_length=2000,  # Discord 免费版限制，Nitro 为 4000
    )

    def __init__(self):
        """初始化 Discord 适配器。"""
        if not _DISCORD_AVAILABLE:
            raise RuntimeError(
                "DiscordAdapter requires 'discord.py'. "
                "Install it with: pip install discord.py"
            )

        super().__init__()

        # account_id -> discord.Client 实例
        self._clients: dict[str, discord.Client] = {}

        # account_id -> 配置字典
        self._configs: dict[str, dict] = {}

        # account_id -> 状态快照
        self._snapshots: dict[str, ChannelAccountSnapshot] = {}

        logger.debug("DiscordAdapter initialized")

    # ── 生命周期管理 ────────────────────────────────────────────────────────────

    async def start(self, account_id: str, config: dict) -> ChannelAccountSnapshot:
        """
        启动 Discord Bot 连接。

        使用配置中的 token 创建 discord.Client 并连接 Discord Gateway。

        Args:
            account_id: 账户标识
            config:     配置字典，必须包含：
                          - token: Bot Token（必填）
                          - intents: intent 列表（可选，默认 ["messages", "guilds", "message_content"]）

        Returns:
            启动后的账户状态快照

        Raises:
            ValueError:  配置缺失或格式错误
            RuntimeError: 连接失败
        """
        token = config.get("token")
        if not token:
            raise ValueError(
                "Discord config missing required field 'token'. "
                "Get your bot token from https://discord.com/developers/applications"
            )

        # 如果已有连接，先停止
        if account_id in self._clients:
            await self.stop(account_id)

        self._configs[account_id] = config

        # 构建 Intents
        intent_names = config.get("intents", ["messages", "guilds", "message_content"])
        intents = self._build_intents(intent_names)

        # 创建 discord.Client
        client = discord.Client(intents=intents)

        # 注册事件处理器
        client.event(self._make_on_message(account_id))
        client.event(self._make_on_ready(account_id))
        client.event(self._make_on_disconnect(account_id))

        self._clients[account_id] = client

        # 初始化快照
        snapshot = ChannelAccountSnapshot(
            account_id=account_id,
            enabled=True,
            configured=True,
            linked=True,
            running=False,
            connected=False,
        )
        self._snapshots[account_id] = snapshot

        # 启动连接（非阻塞）
        logger.info(
            "Discord connecting | account={} intents={}",
            account_id, intent_names,
        )

        try:
            # discord.Client.start() 是阻塞的，我们需要在后台运行
            asyncio.create_task(self._run_client(account_id, token))
        except Exception as e:
            logger.error(
                "Discord start failed | account={} err={}",
                account_id, e,
            )
            snapshot.running = False
            snapshot.connected = False
            snapshot.last_error = str(e)
            raise RuntimeError(f"Discord connection failed: {e}")

        # 等待 on_ready（最多 30 秒）
        for _ in range(60):
            if snapshot.connected:
                break
            await asyncio.sleep(0.5)

        if not snapshot.connected:
            logger.warning(
                "Discord connection timeout | account={}",
                account_id,
            )
            snapshot.last_error = "Connection timeout"

        return snapshot

    async def _run_client(self, account_id: str, token: str) -> None:
        """
        后台运行 discord.Client。

        这是 discord.Client.start() 的包装，捕获异常并更新状态。
        """
        client = self._clients.get(account_id)
        if client is None:
            return

        try:
            await client.start(token)
        except discord.LoginFailure as e:
            logger.error(
                "Discord login failed | account={} err={}",
                account_id, e,
            )
            snapshot = self._snapshots.get(account_id)
            if snapshot:
                snapshot.running = False
                snapshot.connected = False
                snapshot.last_error = f"Login failed: {e}"
        except Exception as e:
            logger.error(
                "Discord client error | account={} err={}",
                account_id, e,
            )
            snapshot = self._snapshots.get(account_id)
            if snapshot:
                snapshot.running = False
                snapshot.connected = False
                snapshot.last_error = str(e)

    async def stop(self, account_id: str) -> None:
        """
        停止 Discord Bot 连接。

        关闭 discord.Client 并清理资源。

        Args:
            account_id: 账户标识
        """
        client = self._clients.pop(account_id, None)
        if client is None:
            return

        logger.info("Discord disconnecting | account={}", account_id)

        try:
            await client.close()
        except Exception as e:
            logger.error(
                "Discord stop error | account={} err={}",
                account_id, e,
            )

        # 更新快照
        snapshot = self._snapshots.get(account_id)
        if snapshot:
            snapshot.running = False
            snapshot.connected = False

        logger.info("Discord disconnected | account={}", account_id)

    # ── 出站消息发送 ────────────────────────────────────────────────────────────

    async def send_text(
        self,
        account_id: str,
        to: str,
        text: str,
        reply_to_id: Optional[str] = None,
    ) -> dict:
        """
        发送纯文本消息到 Discord。

        Args:
            account_id:  发送方 Bot 账户标识
            to:            目标 Discord Channel ID（字符串形式）
            text:          消息文本（Discord Markdown 格式）
            reply_to_id:   要回复的消息 ID（Discord Message ID）

        Returns:
            发送结果字典
        """
        client = self._clients.get(account_id)
        if client is None:
            return {
                "success": False,
                "error": f"Discord client not running for account '{account_id}'",
            }

        # 获取目标 channel
        try:
            channel_id = int(to)
        except ValueError:
            return {
                "success": False,
                "error": f"Invalid Discord channel ID: '{to}'",
            }

        channel = client.get_channel(channel_id)
        if channel is None:
            # 尝试通过 fetch 获取（DM channel 可能需要 fetch）
            try:
                channel = await client.fetch_channel(channel_id)
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Discord channel not found: {to} | {e}",
                }

        if not isinstance(channel, (discord.TextChannel, discord.DMChannel, discord.Thread)):
            return {
                "success": False,
                "error": f"Discord channel type not supported: {type(channel).__name__}",
            }

        # 构建回复引用
        reference = None
        if reply_to_id:
            try:
                reference = discord.MessageReference(
                    message_id=int(reply_to_id),
                    channel_id=channel_id,
                )
            except ValueError:
                logger.warning(
                    "Invalid reply_to_id for Discord | id={}",
                    reply_to_id,
                )

        # 发送消息
        try:
            sent_msg = await channel.send(
                content=text,
                reference=reference,
            )
            return {
                "success": True,
                "platform_message_id": str(sent_msg.id),
                "channel_id": str(sent_msg.channel.id),
            }
        except discord.Forbidden as e:
            logger.error(
                "Discord send forbidden | channel={} err={}",
                to, e,
            )
            return {"success": False, "error": f"Forbidden: {e}"}
        except Exception as e:
            logger.error(
                "Discord send failed | channel={} err={}",
                to, e,
            )
            return {"success": False, "error": str(e)}

    async def send_media(
        self,
        account_id: str,
        to: str,
        media_url: str,
        caption: Optional[str] = None,
    ) -> dict:
        """
        发送媒体消息到 Discord。

        Discord 通过 File 对象发送文件/图片。

        Args:
            account_id:  发送方 Bot 账户标识
            to:            目标 Discord Channel ID
            media_url:     媒体文件 URL（http/https）或本地路径
            caption:       媒体附带的文字说明

        Returns:
            发送结果字典
        """
        client = self._clients.get(account_id)
        if client is None:
            return {
                "success": False,
                "error": f"Discord client not running for account '{account_id}'",
            }

        try:
            channel_id = int(to)
        except ValueError:
            return {
                "success": False,
                "error": f"Invalid Discord channel ID: '{to}'",
            }

        channel = client.get_channel(channel_id)
        if channel is None:
            try:
                channel = await client.fetch_channel(channel_id)
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Discord channel not found: {to} | {e}",
                }

        # 构建 discord.File
        try:
            if media_url.startswith("http://") or media_url.startswith("https://"):
                # URL：下载并发送
                import aiohttp

                async with aiohttp.ClientSession() as session:
                    async with session.get(media_url) as resp:
                        if resp.status != 200:
                            return {
                                "success": False,
                                "error": f"Failed to download media: HTTP {resp.status}",
                            }
                        data = await resp.read()
                        filename = media_url.split("/")[-1] or "attachment"
                        file = discord.File(
                            fp=__import__("io").BytesIO(data),
                            filename=filename,
                        )
            else:
                # 本地路径
                file = discord.File(fp=media_url)

            sent_msg = await channel.send(
                content=caption or "",
                file=file,
            )
            return {
                "success": True,
                "platform_message_id": str(sent_msg.id),
                "channel_id": str(sent_msg.channel.id),
            }
        except Exception as e:
            logger.error(
                "Discord media send failed | channel={} url={} err={}",
                to, media_url, e,
            )
            return {"success": False, "error": str(e)}

    # ── 状态查询 ────────────────────────────────────────────────────────────────

    async def get_status(self, account_id: str) -> ChannelAccountSnapshot:
        """
        查询 Discord Bot 的实时状态。

        Args:
            account_id: 账户标识

        Returns:
            账户状态快照
        """
        snapshot = self._snapshots.get(account_id)
        if snapshot is None:
            return ChannelAccountSnapshot(
                account_id=account_id,
                enabled=False,
                configured=False,
            )

        client = self._clients.get(account_id)
        if client:
            snapshot.connected = client.is_ready()
            snapshot.running = not client.is_closed()

        return snapshot

    # ── Discord 事件处理器（工厂函数）───────────────────────────────────────────

    def _build_intents(self, intent_names: list[str]) -> "Intents":
        """
        根据配置构建 discord.Intents。

        Args:
            intent_names: intent 名称列表

        Returns:
            discord.Intents 实例
        """
        intents = Intents.default()
        intent_map = {
            "messages": lambda i: setattr(i, "guild_messages", True) or setattr(i, "dm_messages", True),
            "guilds": lambda i: setattr(i, "guilds", True),
            "message_content": lambda i: setattr(i, "message_content", True),
            "members": lambda i: setattr(i, "members", True),
            "presences": lambda i: setattr(i, "presences", True),
            "reactions": lambda i: setattr(i, "guild_reactions", True) or setattr(i, "dm_reactions", True),
            "typing": lambda i: setattr(i, "guild_typing", True) or setattr(i, "dm_typing", True),
        }

        for name in intent_names:
            setter = intent_map.get(name)
            if setter:
                setter(intents)
            else:
                logger.warning("Unknown Discord intent: '{}'", name)

        return intents

    def _make_on_ready(self, account_id: str):
        """
        创建 on_ready 事件处理器。

        Bot 成功连接后触发，更新状态快照。
        """
        async def on_ready():
            client = self._clients.get(account_id)
            if client is None:
                return

            snapshot = self._snapshots.get(account_id)
            if snapshot:
                snapshot.running = True
                snapshot.connected = True
                snapshot.last_connected_at = _iso_now()
                snapshot.last_error = None

            logger.info(
                "Discord ready | account={} user={} guilds={}",
                account_id,
                client.user,
                len(client.guilds),
            )

        return on_ready

    def _make_on_disconnect(self, account_id: str):
        """
        创建 on_disconnect 事件处理器。

        连接断开时触发，更新状态快照。
        """
        async def on_disconnect():
            snapshot = self._snapshots.get(account_id)
            if snapshot:
                snapshot.connected = False

            logger.warning(
                "Discord disconnected | account={}",
                account_id,
            )

        return on_disconnect

    def _make_on_message(self, account_id: str):
        """
        创建 on_message 事件处理器。

        收到消息时触发，转换为 InboundMessage 并分发给 Gateway。
        """
        async def on_message(message: discord.Message):
            # 忽略 Bot 自己的消息，避免自循环
            client = self._clients.get(account_id)
            if client and message.author.id == client.user.id:
                return

            # 忽略空消息
            if not message.content:
                return

            # 确定 chat_type
            if isinstance(message.channel, discord.DMChannel):
                chat_type = "direct"
            elif isinstance(message.channel, discord.Thread):
                chat_type = "group"  # Thread 归类为 group
            else:
                chat_type = "channel"

            # 构建 InboundMessage
            inbound = InboundMessage(
                channel_id=self.channel_id,
                account_id=account_id,
                sender_id=str(message.author.id),
                sender_name=message.author.display_name,
                text=message.content,
                chat_type=chat_type,
                thread_id=str(message.channel.id),
                message_id=str(message.id),
                media_urls=[a.url for a in message.attachments],
                timestamp=message.created_time.timestamp() if hasattr(message, 'created_time') else message.created_at.timestamp(),
                raw_payload={
                    "guild_id": str(message.guild.id) if message.guild else None,
                    "author_username": message.author.name,
                    "author_discriminator": getattr(message.author, 'discriminator', '0'),
                    "mention_everyone": message.mention_everyone,
                    "mentions": [str(m.id) for m in message.mentions],
                },
            )

            # 通过 _dispatch_inbound 分发给 Gateway
            await self._dispatch_inbound(inbound)

        return on_message


# ── 辅助函数 ─────────────────────────────────────────────────────────────────


def _iso_now() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()
