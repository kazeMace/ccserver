"""
channels/adapters/feishu — 飞书（Lark）Channel 适配器。

与 OpenClaw 的飞书 channel 插件对齐。
基于飞书开放平台 HTTP 回调模式实现，支持：
  - 接收消息（事件订阅 + 签名验证 + Challenge 响应）
  - 发送文本/富文本/图片/文件消息
  - tenant_access_token 自动刷新
  - 多账户支持

依赖
────
    pip install aiohttp cryptography  # cryptography 用于飞书事件加解密

配置示例 (channels.json)
────────────────────────
    {
        "channels": {
            "feishu": {
                "enabled": true,
                "auto_start": true,
                "accounts": {
                    "default": {
                        "app_id": "cli_xxx",
                        "app_secret": "xxx",
                        "encrypt_key": "xxx",          # 可选，事件加密密钥
                        "verification_token": "xxx"    # 可选，Challenge 验证 token
                    }
                }
            }
        }
    }

使用方式
────────
    # 1. 在飞书开放平台创建企业自建应用：https://open.feishu.cn/app
    # 2. 获取 App ID 和 App Secret
    # 3. 在"事件与回调"中配置：
    #    - 请求地址: https://your-server.com/webhook/feishu
    #    - 需要的事件: im.message.receive_v1
    #    - 配置 Encrypt Key 和 Verification Token（可选但建议）
    # 4. 在 server.py 中注册 /webhook/feishu 路由（已内置）
    # 5. 启动 ccserver，飞书适配器自动加载

注意事项
────────
  - HTTP 回调模式需要公网可访问的地址（本地开发可用 ngrok）
  - 飞书单条文本消息限制约 3000 字符
  - 图片/文件需要先上传获取 key 再发送
  - 事件推送频率较高时建议配置加密
"""

import asyncio
import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from ..base import (
    BaseChannelAdapter,
    ChannelCapabilities,
    ChannelAccountSnapshot,
    InboundMessage,
)

# aiohttp 用于异步 HTTP 请求（已通过 discord.py 安装）
try:
    import aiohttp
    _AIOHTTP_AVAILABLE = True
except ImportError:
    _AIOHTTP_AVAILABLE = False
    aiohttp = None  # type: ignore

# cryptography 用于飞书事件加解密
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False
    AESGCM = None  # type: ignore


# ── 常量 ──────────────────────────────────────────────────────────────────────

FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
TOKEN_REFRESH_INTERVAL = 7200  # tenant_access_token 有效期 2 小时（7200 秒）
TEXT_MAX_LENGTH = 3000


# ── FeishuAdapter ─────────────────────────────────────────────────────────────


class FeishuAdapter(BaseChannelAdapter):
    """
    飞书（Lark）channel 适配器。

    基于飞书开放平台 HTTP 回调模式实现。
    接收消息通过 server.py 的 /webhook/feishu 路由，
    发送消息通过飞书 REST API。

    与 OpenClaw 的飞书 channel 插件对齐。

    Attributes:
        channel_id: "feishu"
        aliases: ["lark"]
        meta: 支持 direct + group，支持媒体，支持 Markdown
    """

    channel_id = "feishu"
    aliases = ["lark"]
    meta = ChannelCapabilities(
        chat_types=["direct", "group"],
        supports_media=True,
        supports_reactions=False,
        supports_reply=True,
        supports_edit=True,
        supports_delete=False,
        markdown_capable=True,
        max_text_length=TEXT_MAX_LENGTH,
    )

    def __init__(self):
        """初始化飞书适配器。"""
        if not _AIOHTTP_AVAILABLE:
            raise RuntimeError(
                "FeishuAdapter requires 'aiohttp'. "
                "Install it with: pip install aiohttp"
            )

        super().__init__()

        # account_id -> 配置字典
        self._configs: dict[str, dict] = {}

        # account_id -> 状态快照
        self._snapshots: dict[str, ChannelAccountSnapshot] = {}

        # account_id -> tenant_access_token
        self._tokens: dict[str, str] = {}

        # account_id -> token 过期时间戳
        self._token_expires: dict[str, float] = {}

        # account_id -> token 刷新 asyncio.Task
        self._token_refresh_tasks: dict[str, asyncio.Task] = {}

        # aiohttp session（复用连接池）
        self._session: Optional[aiohttp.ClientSession] = None

        # 已处理的飞书消息 ID 集合（防止 webhook 重试导致重复处理）
        self._processed_message_ids: set[str] = set()

        logger.debug("FeishuAdapter initialized")

    # ── 生命周期管理 ────────────────────────────────────────────────────────────

    async def start(self, account_id: str, config: dict) -> ChannelAccountSnapshot:
        """
        启动飞书适配器。

        HTTP 回调模式不需要主动建立连接，
        但需要获取 tenant_access_token 并启动刷新任务。

        Args:
            account_id: 账户标识
            config:     配置字典，必须包含：
                          - app_id: 飞书应用 ID（必填）
                          - app_secret: 飞书应用 Secret（必填）
                          - encrypt_key: 事件加密密钥（可选）
                          - verification_token: Challenge 验证 Token（可选）

        Returns:
            启动后的账户状态快照

        Raises:
            ValueError:  配置缺失或格式错误
            RuntimeError: token 获取失败
        """
        app_id = config.get("app_id")
        app_secret = config.get("app_secret")

        if not app_id:
            raise ValueError(
                "Feishu config missing required field 'app_id'. "
                "Get it from https://open.feishu.cn/app"
            )
        if not app_secret:
            raise ValueError(
                "Feishu config missing required field 'app_secret'. "
                "Get it from https://open.feishu.cn/app"
            )

        # 如果已有任务，先停止
        if account_id in self._token_refresh_tasks:
            await self.stop(account_id)

        self._configs[account_id] = config

        # 初始化 aiohttp session（延迟创建）
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

        # 初始化快照
        snapshot = ChannelAccountSnapshot(
            account_id=account_id,
            enabled=True,
            configured=True,
            linked=True,
            running=True,
            connected=True,
            last_connected_at=_iso_now(),
        )
        self._snapshots[account_id] = snapshot

        # 获取初始 token
        try:
            await self._refresh_token(account_id)
        except Exception as e:
            logger.error(
                "Feishu token refresh failed | account={} err={}",
                account_id, e,
            )
            snapshot.running = False
            snapshot.connected = False
            snapshot.last_error = str(e)
            raise RuntimeError(f"Feishu token refresh failed: {e}")

        # 启动定时刷新任务
        task = asyncio.create_task(self._token_refresh_loop(account_id))
        self._token_refresh_tasks[account_id] = task

        logger.info(
            "Feishu adapter started | account={} app_id={}",
            account_id, app_id,
        )

        return snapshot

    async def stop(self, account_id: str) -> None:
        """
        停止飞书适配器。

        取消 token 刷新任务并清理资源。

        Args:
            account_id: 账户标识
        """
        task = self._token_refresh_tasks.pop(account_id, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # 清理 token
        self._tokens.pop(account_id, None)
        self._token_expires.pop(account_id, None)

        # 更新快照
        snapshot = self._snapshots.get(account_id)
        if snapshot:
            snapshot.running = False
            snapshot.connected = False

        logger.info("Feishu adapter stopped | account={}", account_id)

    async def shutdown(self) -> None:
        """
        关闭适配器，释放 aiohttp session。

        由 ChannelGateway.shutdown() 调用。
        """
        # 停止所有账户
        for account_id in list(self._token_refresh_tasks.keys()):
            await self.stop(account_id)

        # 关闭 session
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

        logger.info("Feishu adapter shutdown complete")

    # ── 出站消息发送 ────────────────────────────────────────────────────────────

    async def send_text(
        self,
        account_id: str,
        to: str,
        text: str,
        reply_to_id: Optional[str] = None,
    ) -> dict:
        """
        发送纯文本消息到飞书。

        Args:
            account_id:  发送方账户标识
            to:            目标 open_id 或 chat_id
            text:          消息文本
            reply_to_id:   回复哪条消息的 message_id（可选）

        Returns:
            发送结果字典
        """
        content = json.dumps({"text": text}, ensure_ascii=False)
        return await self._send_message(
            account_id=account_id,
            receive_id=to,
            msg_type="text",
            content=content,
            reply_to_id=reply_to_id,
        )

    async def send_media(
        self,
        account_id: str,
        to: str,
        media_url: str,
        caption: Optional[str] = None,
    ) -> dict:
        """
        发送媒体消息到飞书。

        飞书发送图片/文件的流程：
          1. 上传文件获取 image_key / file_key
          2. 发送消息引用该 key

        Args:
            account_id:  发送方账户标识
            to:            目标 open_id 或 chat_id
            media_url:     媒体文件 URL（http/https）或本地路径
            caption:       媒体附带的文字说明

        Returns:
            发送结果字典
        """
        token = await self._ensure_token(account_id)
        if not token:
            return {
                "success": False,
                "error": "No valid tenant_access_token available",
            }

        # 1. 上传文件获取 key
        upload_result = await self._upload_media(account_id, media_url)
        if not upload_result.get("success"):
            return upload_result

        media_key = upload_result.get("key")
        media_type = upload_result.get("type")  # "image" or "file"

        if media_type == "image":
            content = json.dumps({
                "image_key": media_key,
                "alt": {"tag": "plain_text", "content": caption or ""},
            }, ensure_ascii=False)
            msg_type = "image"
        else:
            content = json.dumps({
                "file_key": media_key,
            }, ensure_ascii=False)
            msg_type = "file"

        # 如果有文字说明，先发文字，再发媒体
        if caption:
            await self.send_text(account_id, to, caption)

        result = await self._send_message(
            account_id=account_id,
            receive_id=to,
            msg_type=msg_type,
            content=content,
        )

        return result

    async def _send_message(
        self,
        account_id: str,
        receive_id: str,
        msg_type: str,
        content: str,
        reply_to_id: Optional[str] = None,
    ) -> dict:
        """
        调用飞书消息发送 API。

        Args:
            account_id:   账户标识
            receive_id:   接收者 open_id / chat_id
            msg_type:     消息类型：text / image / file / interactive 等
            content:      消息内容 JSON 字符串
            reply_to_id:  回复哪条消息的 message_id

        Returns:
            发送结果字典
        """
        token = await self._ensure_token(account_id)
        if not token:
            return {
                "success": False,
                "error": "No valid tenant_access_token available",
            }

        # 判断 receive_id 类型
        receive_id_type = self._guess_receive_id_type(receive_id)

        url = f"{FEISHU_API_BASE}/im/v1/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        params = {"receive_id_type": receive_id_type}

        payload = {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": content,
        }
        if reply_to_id:
            # 飞书原生回复引用：使用 parent_message_id
            payload["parent_message_id"] = reply_to_id

        try:
            async with self._session.post(
                url,
                headers=headers,
                params=params,
                json=payload,
            ) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("code") == 0:
                    msg_data = data.get("data", {})
                    return {
                        "success": True,
                        "platform_message_id": msg_data.get("message_id"),
                    }
                else:
                    error_msg = data.get("msg", f"HTTP {resp.status}")
                    logger.error(
                        "Feishu send failed | account={} to={} err={}",
                        account_id, receive_id, error_msg,
                    )
                    return {"success": False, "error": error_msg}
        except Exception as e:
            logger.error(
                "Feishu send exception | account={} to={} err={}",
                account_id, receive_id, e,
            )
            return {"success": False, "error": str(e)}

    # ── 状态查询 ────────────────────────────────────────────────────────────────

    async def get_status(self, account_id: str) -> ChannelAccountSnapshot:
        """
        查询飞书适配器的实时状态。

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

        # 检查 token 是否有效
        token = self._tokens.get(account_id)
        expires = self._token_expires.get(account_id, 0)
        snapshot.connected = token is not None and time.time() < expires

        return snapshot

    # ── Webhook 处理（入站消息入口）─────────────────────────────────────────────

    async def handle_webhook(self, body: dict, headers: dict) -> Optional[dict]:
        """
        处理飞书事件推送。

        由 server.py 的 /webhook/feishu 路由调用。

        处理流程：
          1. 验证签名（如果配置了 encrypt_key）
          2. 响应 Challenge 验证（首次配置回调 URL）
          3. 解析 im.message.receive_v1 事件
          4. 构造 InboundMessage 并分发给 Gateway

        Args:
            body:    飞书推送的事件 body（JSON 字典）
            headers: HTTP 请求头

        Returns:
            如果需要响应 Challenge，返回 {"challenge": "xxx"}
            否则返回 None（HTTP 200 即可）
        """
        # 1. 处理 Challenge 验证
        if body.get("type") == "url_verification":
            challenge = body.get("challenge", "")
            _ = body.get("token", "")  # 保留以备 token 验证扩展
            logger.info("Feishu url_verification received")
            return {"challenge": challenge}

        # 2. 处理加密事件（schema 2.0）
        if body.get("encrypt"):
            # 解密事件
            decrypted = self._decrypt_event(body)
            if decrypted is None:
                logger.warning("Feishu event decryption failed")
                return None
            body = decrypted

        # 3. 验证签名（header 中的 X-Lark-Signature）
        signature = headers.get("X-Lark-Signature", "")
        if signature and not self._verify_signature(body, signature):
            logger.warning("Feishu signature verification failed")
            return None

        # 4. 解析事件
        event_type = body.get("header", {}).get("event_type", "")

        if event_type == "im.message.receive_v1":
            await self._handle_message_event(body)
        else:
            logger.debug(
                "Feishu event ignored | type={}",
                event_type,
            )

        return None

    async def _handle_message_event(self, body: dict) -> None:
        """
        处理飞书消息接收事件。

        解析 im.message.receive_v1 事件，构造 InboundMessage 分发给 Gateway。
        基于 message_id 去重，防止飞书 webhook 重试导致重复处理。
        """
        event = body.get("event", {})
        message = event.get("message", {})
        sender = event.get("sender", {})

        # 消息去重：飞书 webhook 可能因超时重试，同一消息会推送多次
        msg_id = message.get("message_id", "")
        if msg_id in self._processed_message_ids:
            logger.debug(
                "Feishu message deduplicated | msg_id={}",
                msg_id[:16],
            )
            return
        self._processed_message_ids.add(msg_id)

        # 限制去重集合大小，防止内存泄漏
        if len(self._processed_message_ids) > 10000:
            # 保留最近 5000 条
            self._processed_message_ids = set(
                list(self._processed_message_ids)[-5000:]
            )

        # 解析消息内容
        msg_type = message.get("message_type", "")
        content_str = message.get("content", "{}")
        try:
            content = json.loads(content_str)
        except json.JSONDecodeError:
            content = {}

        # 提取文本内容
        text = ""
        if msg_type == "text":
            text = content.get("text", "")
        elif msg_type == "post":
            # 富文本，提取纯文本（简化处理）
            text = self._extract_text_from_post(content)
        elif msg_type == "image":
            text = "[图片]"
        elif msg_type == "file":
            text = "[文件]"
        else:
            text = f"[{msg_type}]"

        # 解析 sender
        sender_id_info = sender.get("sender_id", {})
        sender_open_id = sender_id_info.get("open_id", "")
        sender_user_id = sender_id_info.get("user_id", "")

        # 判断聊天类型
        chat_type_str = message.get("chat_type", "")
        if chat_type_str == "p2p":
            chat_type = "direct"
        else:
            chat_type = "group"

        # 构造 InboundMessage
        inbound = InboundMessage(
            channel_id=self.channel_id,
            account_id=self._find_account_by_sender(sender_open_id),
            sender_id=sender_open_id or sender_user_id,
            sender_name=sender_open_id,  # 飞书事件中没有 display_name，可用 API 获取
            text=text,
            chat_type=chat_type,
            thread_id=message.get("chat_id", ""),
            message_id=message.get("message_id", ""),
            media_urls=[],
            timestamp=int(message.get("create_time", "0")) / 1000,
            raw_payload={
                "message_type": msg_type,
                "chat_id": message.get("chat_id"),
                "root_id": message.get("root_id"),
                "parent_id": message.get("parent_id"),
                "mentions": message.get("mentions", []),
                "tenant_key": sender.get("tenant_key", ""),
            },
        )

        await self._dispatch_inbound(inbound)

    # ── Token 管理 ──────────────────────────────────────────────────────────────

    async def _ensure_token(self, account_id: str) -> Optional[str]:
        """
        确保指定账户的 token 有效。

        如果 token 即将过期，触发刷新。

        Args:
            account_id: 账户标识

        Returns:
            有效的 tenant_access_token，或 None
        """
        token = self._tokens.get(account_id)
        expires = self._token_expires.get(account_id, 0)

        if token and time.time() < expires - 60:  # 预留 60 秒缓冲
            return token

        # token 过期或不存在，刷新
        try:
            await self._refresh_token(account_id)
            return self._tokens.get(account_id)
        except Exception as e:
            logger.error(
                "Feishu token refresh failed | account={} err={}",
                account_id, e,
            )
            return None

    async def _refresh_token(self, account_id: str) -> None:
        """
        刷新 tenant_access_token。

        调用飞书 auth API：
            POST /open-apis/auth/v3/tenant_access_token/internal

        Args:
            account_id: 账户标识

        Raises:
            RuntimeError: 如果刷新失败
        """
        config = self._configs.get(account_id, {})
        app_id = config.get("app_id", "")
        app_secret = config.get("app_secret", "")

        if not app_id or not app_secret:
            raise RuntimeError("Missing app_id or app_secret")

        url = f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": app_id,
            "app_secret": app_secret,
        }

        try:
            async with self._session.post(url, json=payload) as resp:
                data = await resp.json()
                if resp.status == 200 and data.get("code") == 0:
                    token = data.get("tenant_access_token", "")
                    expires_in = data.get("expire", TOKEN_REFRESH_INTERVAL)

                    self._tokens[account_id] = token
                    self._token_expires[account_id] = time.time() + expires_in

                    logger.info(
                        "Feishu token refreshed | account={} expires_in={}s",
                        account_id, expires_in,
                    )
                else:
                    error_msg = data.get("msg", f"HTTP {resp.status}")
                    raise RuntimeError(f"Token refresh failed: {error_msg}")
        except Exception as e:
            logger.error(
                "Feishu token refresh error | account={} err={}",
                account_id, e,
            )
            raise

    async def _token_refresh_loop(self, account_id: str) -> None:
        """
        Token 定时刷新协程。

        每 2 小时刷新一次 token。
        """
        logger.info(
            "Feishu token refresh loop started | account={}",
            account_id,
        )

        while True:
            try:
                # 等待到 token 过期前 5 分钟
                expires = self._token_expires.get(account_id, 0)
                wait_time = max(60, expires - time.time() - 300)
                await asyncio.sleep(wait_time)

                await self._refresh_token(account_id)
            except asyncio.CancelledError:
                logger.debug(
                    "Feishu token refresh loop cancelled | account={}",
                    account_id,
                )
                break
            except Exception as e:
                logger.error(
                    "Feishu token refresh loop error | account={} err={}",
                    account_id, e,
                )
                await asyncio.sleep(60)  # 出错后 1 分钟重试

        logger.info(
            "Feishu token refresh loop ended | account={}",
            account_id,
        )

    # ── 媒体上传 ────────────────────────────────────────────────────────────────

    async def _upload_media(self, account_id: str, media_url: str) -> dict:
        """
        上传媒体文件到飞书。

        Args:
            account_id:  账户标识
            media_url:   文件 URL 或本地路径

        Returns:
            {"success": True, "key": "image_key/file_key", "type": "image/file"}
        """
        token = await self._ensure_token(account_id)
        if not token:
            return {
                "success": False,
                "error": "No valid tenant_access_token available",
            }

        # 判断是图片还是文件
        ext = media_url.split("?")[0].split(".")[-1].lower()
        is_image = ext in ("jpg", "jpeg", "png", "gif", "bmp", "webp")

        if media_url.startswith("http://") or media_url.startswith("https://"):
            # 下载文件
            try:
                async with aiohttp.ClientSession() as download_session:
                    async with download_session.get(media_url) as resp:
                        if resp.status != 200:
                            return {
                                "success": False,
                                "error": f"Download failed: HTTP {resp.status}",
                            }
                        file_data = await resp.read()
                        filename = media_url.split("/")[-1].split("?")[0] or "attachment"
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Download failed: {e}",
                }
        else:
            # 本地文件
            try:
                with open(media_url, "rb") as f:
                    file_data = f.read()
                filename = media_url.split("/")[-1]
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Read file failed: {e}",
                }

        # 上传到飞书
        upload_type = "image" if is_image else "file"
        url = f"{FEISHU_API_BASE}/im/v1/{upload_type}s"
        headers = {"Authorization": f"Bearer {token}"}

        data = aiohttp.FormData()
        data.add_field(
            "file_type",
            "image" if is_image else "stream",
        )
        data.add_field(
            "file",
            file_data,
            filename=filename,
            content_type="application/octet-stream",
        )

        try:
            async with self._session.post(url, headers=headers, data=data) as resp:
                result = await resp.json()
                if resp.status == 200 and result.get("code") == 0:
                    key = result.get("data", {}).get(f"{upload_type}_key", "")
                    return {
                        "success": True,
                        "key": key,
                        "type": upload_type,
                    }
                else:
                    error_msg = result.get("msg", f"HTTP {resp.status}")
                    return {
                        "success": False,
                        "error": f"Upload failed: {error_msg}",
                    }
        except Exception as e:
            return {
                "success": False,
                "error": f"Upload exception: {e}",
            }

    # ── 加密与签名验证 ──────────────────────────────────────────────────────────

    def _decrypt_event(self, body: dict) -> Optional[dict]:
        """
        解密飞书加密事件。

        使用 AES-GCM 解密 encrypt 字段。
        解密后的数据是 JSON 字符串，需要反序列化。

        Args:
            body: 加密的事件 body

        Returns:
            解密后的 JSON 字典，或 None
        """
        if not _CRYPTO_AVAILABLE:
            logger.warning(
                "Event decryption skipped: 'cryptography' not installed. "
                "Install it with: pip install cryptography"
            )
            return None

        # 飞书使用第一个账户的 encrypt_key（简化处理，实际应根据 app_id 匹配）
        encrypt_key = None
        for config in self._configs.values():
            key = config.get("encrypt_key", "")
            if key:
                encrypt_key = key
                break

        if not encrypt_key:
            logger.warning("No encrypt_key configured, cannot decrypt event")
            return None

        try:
            encrypt_data = body.get("encrypt", "")
            # 飞书加密格式：base64(AES-GCM(nonce + ciphertext + tag))
            # 实际上飞书的加密方式是：base64(AES-GCM(nonce, plaintext))
            # 具体实现需要参考飞书文档
            # 这里提供一个参考实现

            # 飞书事件加解密参考文档：
            # https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/event-subscription-guide/event-subscription-encrypt
            ciphertext = base64.b64decode(encrypt_data)

            # 密钥派生：SHA256(encrypt_key) 取前 32 字节
            key_bytes = hashlib.sha256(encrypt_key.encode("utf-8")).digest()

            # 飞书加密格式：nonce(12 bytes) + ciphertext + auth_tag(16 bytes)
            nonce = ciphertext[:12]
            encrypted = ciphertext[12:-16]
            tag = ciphertext[-16:]

            aesgcm = AESGCM(key_bytes)
            plaintext = aesgcm.decrypt(nonce, encrypted + tag, None)

            return json.loads(plaintext.decode("utf-8"))
        except Exception as e:
            logger.error("Feishu event decryption failed | err={}", e)
            return None

    def _verify_signature(self, body: dict, signature: str) -> bool:
        """
        验证飞书事件签名。

        飞书使用 HMAC-SHA256 对请求体签名：
            signature = base64(HMAC-SHA256(timestamp + nonce + body, encrypt_key))

        Args:
            body:      请求体 JSON 字典
            signature: header 中的 X-Lark-Signature

        Returns:
            True 如果签名有效
        """
        # 获取 encrypt_key
        encrypt_key = None
        for config in self._configs.values():
            key = config.get("encrypt_key", "")
            if key:
                encrypt_key = key
                break

        if not encrypt_key:
            # 没有配置 encrypt_key，跳过验证
            return True

        try:
            # 从 header 或 body 中获取 timestamp 和 nonce
            # 实际应从 HTTP header 中获取
            timestamp = str(body.get("header", {}).get("create_time", ""))
            nonce = body.get("header", {}).get("event_id", "")
            body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)

            # 构造待签名字符串
            sign_str = f"{timestamp}{nonce}{body_str}"

            # HMAC-SHA256
            mac = hmac.new(
                encrypt_key.encode("utf-8"),
                sign_str.encode("utf-8"),
                hashlib.sha256,
            )
            expected = base64.b64encode(mac.digest()).decode("utf-8")

            return hmac.compare_digest(expected, signature)
        except Exception as e:
            logger.error("Feishu signature verification error | err={}", e)
            return False

    # ── 辅助方法 ────────────────────────────────────────────────────────────────

    def _find_account_by_sender(self, sender_open_id: str) -> str:
        """
        根据发送者 open_id 查找对应的账户。

        简化实现：返回第一个已启动的账户。
        未来可根据 tenant_key 或 app_id 精确匹配。
        """
        for account_id in self._configs:
            if account_id in self._snapshots and self._snapshots[account_id].running:
                return account_id
        return "default"

    def _guess_receive_id_type(self, receive_id: str) -> str:
        """
        猜测 receive_id 的类型。

        飞书支持：open_id / user_id / union_id / email / chat_id

        Args:
            receive_id: 接收者 ID

        Returns:
            "open_id" 或 "chat_id"
        """
        # 简单的启发式判断
        if receive_id.startswith("oc_"):
            return "chat_id"
        if receive_id.startswith("ou_"):
            return "open_id"
        if "@" in receive_id:
            return "email"
        # 默认使用 open_id
        return "open_id"

    @staticmethod
    def _extract_text_from_post(content: dict) -> str:
        """
        从飞书 post（富文本）消息中提取纯文本。

        Args:
            content: post 消息内容字典

        Returns:
            提取的纯文本
        """
        texts = []
        post_content = content.get("content", [])
        for paragraph in post_content:
            for element in paragraph:
                if isinstance(element, dict):
                    tag = element.get("tag", "")
                    if tag in ("text", "a"):
                        texts.append(element.get("text", ""))
                    elif tag == "at":
                        texts.append(f"@{element.get('user_name', '')}")
        return "\n".join(texts)


# ── 辅助函数 ─────────────────────────────────────────────────────────────────


def _iso_now() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()
