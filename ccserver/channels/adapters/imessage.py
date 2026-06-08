"""
channels/adapters/imessage — iMessage Channel 适配器。

⚠️  macOS 独占 ⚠️

与 OpenClaw 的 iMessage channel 插件对齐。
基于 AppleScript + SQLite 轮询实现，支持：
  - 通过 osascript 发送文本消息
  - 轮询 ~/Library/Messages/chat.db 接收新消息
  - 连接状态管理

依赖
────
    无额外 Python 依赖（使用标准库 sqlite3 和 subprocess）

配置示例 (channels.json)
────────────────────────
    {
        "channels": {
            "imessage": {
                "enabled": true,
                "auto_start": true,
                "accounts": {
                    "default": {
                        "poll_interval": 5.0,
                        "my_handle": "+8613800138000"
                    }
                }
            }
        }
    }

使用方式
────────
    # 1. 确保 ccserver 运行在 macOS 上
    # 2. 给运行 ccserver 的终端/应用授予"完整磁盘访问权限"：
    #    系统设置 → 隐私与安全 → 完整磁盘访问权限 → 添加 Terminal/VS Code
    # 3. 启动 ccserver，iMessage 适配器自动开始轮询

注意事项
────────
  - 仅支持 macOS（iMessage 是 Apple 独占服务）
  - 发送消息依赖 AppleScript，新版 macOS 可能受限
  - 读取 chat.db 需要"完整磁盘访问权限"
  - 数据库会被系统锁定，采用复制后读取的策略
  - 不支持发送/接收媒体（图片、视频等）
  - 单条消息无长度限制（由 iMessage 自身处理）
"""

import asyncio
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from ..base import (
    BaseChannelAdapter,
    ChannelCapabilities,
    ChannelAccountSnapshot,
    InboundMessage,
)


# ── iMessageAdapter ───────────────────────────────────────────────────────────


class IMessageAdapter(BaseChannelAdapter):
    """
    iMessage channel 适配器（macOS 独占）。

    基于 AppleScript 发送消息，基于 SQLite 轮询接收消息。

    与 OpenClaw 的 iMessage channel 插件对齐。

    Attributes:
        channel_id: "imessage"
        aliases: ["imsg", "sms"]
        meta: 仅支持 direct，不支持媒体
    """

    channel_id = "imessage"
    aliases = ["imsg", "sms"]
    meta = ChannelCapabilities(
        chat_types=["direct"],
        supports_media=False,
        supports_reactions=False,
        supports_reply=False,
        supports_edit=False,
        supports_delete=False,
        markdown_capable=False,
        max_text_length=10_000,  # iMessage 无明确限制
    )

    # macOS Messages 数据库路径
    CHAT_DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"

    def __init__(self):
        """初始化 iMessage 适配器。"""
        super().__init__()

        # 检查是否在 macOS 上运行
        self._is_macos = os.uname().sysname == "Darwin" if hasattr(os, "uname") else False
        if not self._is_macos:
            logger.warning(
                "iMessageAdapter: not running on macOS. "
                "This adapter only works on macOS."
            )

        # account_id -> 配置字典
        self._configs: dict[str, dict] = {}

        # account_id -> 状态快照
        self._snapshots: dict[str, ChannelAccountSnapshot] = {}

        # account_id -> 轮询 asyncio.Task
        self._poll_tasks: dict[str, asyncio.Task] = {}

        # account_id -> 最后处理的消息 ROWID
        self._last_rowids: dict[str, int] = {}

        # account_id -> 本机 handle（用于过滤自己发出的消息）
        self._my_handles: dict[str, set[str]] = {}

        logger.debug(
            "IMessageAdapter initialized | macos={}",
            self._is_macos,
        )

    # ── 生命周期管理 ────────────────────────────────────────────────────────────

    async def start(self, account_id: str, config: dict) -> ChannelAccountSnapshot:
        """
        启动 iMessage 轮询。

        检查环境后开始后台轮询任务，定期读取 chat.db 中的新消息。

        Args:
            account_id: 账户标识
            config:     配置字典，包含：
                          - poll_interval: 轮询间隔（秒，默认 5.0）
                          - my_handle: 本机电话号码或邮箱（用于过滤自己的消息）

        Returns:
            启动后的账户状态快照

        Raises:
            RuntimeError: 如果不是 macOS 或无法访问 chat.db
        """
        if not self._is_macos:
            raise RuntimeError(
                "iMessageAdapter only works on macOS. "
                "Current OS is not macOS."
            )

        if not self.CHAT_DB_PATH.exists():
            raise RuntimeError(
                f"iMessage database not found: {self.CHAT_DB_PATH}. "
                f"Make sure you are on macOS and Messages app has been used."
            )

        # 如果已有轮询，先停止
        if account_id in self._poll_tasks:
            await self.stop(account_id)

        self._configs[account_id] = config

        # 解析本机 handle（用于过滤自己发送的消息）
        my_handle = config.get("my_handle", "")
        self._my_handles[account_id] = set()
        if my_handle:
            self._my_handles[account_id].add(my_handle)
            # 同时尝试从数据库读取本机 handle
            try:
                handles = await self._fetch_my_handles()
                self._my_handles[account_id].update(handles)
            except Exception as e:
                logger.warning(
                    "iMessage: failed to fetch my handles | err={}",
                    e,
                )

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

        # 初始化最后读取的 ROWID
        try:
            self._last_rowids[account_id] = await self._get_max_rowid()
        except Exception as e:
            logger.warning(
                "iMessage: failed to get max rowid | err={}",
                e,
            )
            self._last_rowids[account_id] = 0

        # 启动后台轮询任务
        poll_interval = config.get("poll_interval", 5.0)
        task = asyncio.create_task(
            self._poll_loop(account_id, poll_interval)
        )
        self._poll_tasks[account_id] = task

        logger.info(
            "iMessage polling started | account={} interval={}s my_handles={}",
            account_id, poll_interval, self._my_handles[account_id],
        )

        return snapshot

    async def stop(self, account_id: str) -> None:
        """
        停止 iMessage 轮询。

        取消后台轮询任务并清理资源。

        Args:
            account_id: 账户标识
        """
        task = self._poll_tasks.pop(account_id, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # 更新快照
        snapshot = self._snapshots.get(account_id)
        if snapshot:
            snapshot.running = False
            snapshot.connected = False

        logger.info("iMessage polling stopped | account={}", account_id)

    # ── 出站消息发送 ────────────────────────────────────────────────────────────

    async def send_text(
        self,
        account_id: str,
        to: str,
        text: str,
        reply_to_id: Optional[str] = None,
    ) -> dict:
        """
        通过 AppleScript 发送 iMessage。

        使用 osascript 调用 Messages app 发送消息。
        注意：新版 macOS 可能对 AppleScript 访问 Messages 有限制。

        Args:
            account_id:  发送方账户标识
            to:            目标联系人（电话号码或 Apple ID 邮箱）
            text:          消息文本
            reply_to_id:   回复消息 ID（iMessage 不支持，忽略）

        Returns:
            发送结果字典
        """
        if not self._is_macos:
            return {
                "success": False,
                "error": "iMessage sending only works on macOS",
            }

        # 构建 AppleScript
        # 注意：使用 'buddy' 方式发送，to 参数是 handle（电话或邮箱）
        script = f'''
tell application "Messages"
    set targetService to 1st service whose service type = iMessage
    set targetBuddy to buddy "{to}" of targetService
    send "{text}" to targetBuddy
end tell
'''

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    text=True,
                    timeout=30,
                ),
            )

            if result.returncode != 0:
                error_msg = result.stderr.strip() if result.stderr else "AppleScript execution failed"
                logger.error(
                    "iMessage send failed | to={} err={}",
                    to, error_msg,
                )
                return {
                    "success": False,
                    "error": f"AppleScript error: {error_msg}",
                }

            logger.info(
                "iMessage sent | to={} text_len={}",
                to, len(text),
            )
            return {
                "success": True,
                "platform_message_id": None,  # AppleScript 不返回 message ID
            }

        except subprocess.TimeoutExpired:
            logger.error("iMessage send timeout | to={}", to)
            return {"success": False, "error": "AppleScript timeout"}
        except Exception as e:
            logger.error("iMessage send failed | to={} err={}", to, e)
            return {"success": False, "error": str(e)}

    # ── 状态查询 ────────────────────────────────────────────────────────────────

    async def get_status(self, account_id: str) -> ChannelAccountSnapshot:
        """
        查询 iMessage 适配器的实时状态。

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

        task = self._poll_tasks.get(account_id)
        if task is not None:
            snapshot.running = not task.done()
            snapshot.connected = not task.done()

        return snapshot

    # ── 轮询逻辑 ────────────────────────────────────────────────────────────────

    async def _poll_loop(self, account_id: str, interval: float) -> None:
        """
        后台轮询协程。

        定期读取 chat.db 中的新消息，转换为 InboundMessage 分发给 Gateway。

        Args:
            account_id:  账户标识
            interval:    轮询间隔（秒）
        """
        logger.info(
            "iMessage poll loop started | account={} interval={}s",
            account_id, interval,
        )

        while True:
            try:
                await self._poll_once(account_id)
            except asyncio.CancelledError:
                logger.debug("iMessage poll loop cancelled | account={}", account_id)
                break
            except Exception as e:
                logger.error(
                    "iMessage poll error | account={} err={}",
                    account_id, e,
                )

            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

        logger.info("iMessage poll loop ended | account={}", account_id)

    async def _poll_once(self, account_id: str) -> None:
        """
        执行一次轮询。

        读取 chat.db 中 ROWID 大于 last_rowid 的新消息，
        过滤掉自己发送的消息，分发给 Gateway。
        """
        messages = await self._fetch_new_messages(account_id)
        if not messages:
            return

        my_handles = self._my_handles.get(account_id, set())

        for msg in messages:
            # 过滤自己发送的消息（避免自循环）
            sender_handle = msg.get("sender_handle", "")
            if sender_handle in my_handles:
                continue

            # 过滤空消息
            text = msg.get("text", "")
            if not text:
                continue

            inbound = InboundMessage(
                channel_id=self.channel_id,
                account_id=account_id,
                sender_id=sender_handle or str(msg.get("handle_id", "")),
                sender_name=sender_handle or "Unknown",
                text=text,
                chat_type="direct",
                thread_id=sender_handle,
                message_id=str(msg.get("rowid", "")),
                timestamp=msg.get("date", time.time()),
                raw_payload={
                    "is_from_me": msg.get("is_from_me", False),
                    "service": msg.get("service", ""),
                    "handle_id": msg.get("handle_id"),
                },
            )

            await self._dispatch_inbound(inbound)

    # ── SQLite 数据库操作 ───────────────────────────────────────────────────────

    async def _fetch_new_messages(self, account_id: str) -> list[dict]:
        """
        从 chat.db 读取新消息。

        采用复制数据库后读取的策略（避免文件锁）。

        Returns:
            消息字典列表
        """
        last_rowid = self._last_rowids.get(account_id, 0)

        # 复制数据库到临时文件（避免锁）
        temp_db = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                temp_db = tmp.name

            # 在 executor 中执行文件复制和数据库查询（阻塞操作）
            loop = asyncio.get_event_loop()
            messages, max_rowid = await loop.run_in_executor(
                None,
                self._query_db,
                temp_db,
                last_rowid,
            )

            # 更新最后读取的 ROWID
            if max_rowid > last_rowid:
                self._last_rowids[account_id] = max_rowid

            return messages

        finally:
            if temp_db and os.path.exists(temp_db):
                try:
                    os.unlink(temp_db)
                except Exception:
                    pass

    def _query_db(self, temp_db: str, last_rowid: int) -> tuple[list[dict], int]:
        """
        执行数据库查询（同步方法，在 executor 中运行）。

        Args:
            temp_db:    临时数据库文件路径
            last_rowid: 上次读取的最大 ROWID

        Returns:
            (消息列表, 本次最大 ROWID)
        """
        # 复制数据库
        shutil.copy2(str(self.CHAT_DB_PATH), temp_db)

        conn = sqlite3.connect(temp_db)
        conn.row_factory = sqlite3.Row

        try:
            cursor = conn.execute(
                """
                SELECT
                    m.ROWID as rowid,
                    m.text,
                    m.date,
                    m.is_from_me,
                    m.service,
                    m.handle_id,
                    h.id as sender_handle
                FROM message m
                LEFT JOIN handle h ON m.handle_id = h.ROWID
                WHERE m.ROWID > ?
                ORDER BY m.ROWID ASC
                """,
                (last_rowid,),
            )

            messages = []
            max_rowid = last_rowid

            for row in cursor.fetchall():
                msg = dict(row)
                messages.append(msg)
                if msg["rowid"] > max_rowid:
                    max_rowid = msg["rowid"]

            return messages, max_rowid

        finally:
            conn.close()

    async def _get_max_rowid(self) -> int:
        """获取 chat.db 中当前最大 ROWID。"""
        temp_db = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                temp_db = tmp.name

            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                self._query_max_rowid,
                temp_db,
            )
        finally:
            if temp_db and os.path.exists(temp_db):
                try:
                    os.unlink(temp_db)
                except Exception:
                    pass

    def _query_max_rowid(self, temp_db: str) -> int:
        """同步查询最大 ROWID。"""
        shutil.copy2(str(self.CHAT_DB_PATH), temp_db)
        conn = sqlite3.connect(temp_db)
        try:
            cursor = conn.execute("SELECT MAX(ROWID) FROM message")
            result = cursor.fetchone()
            return result[0] or 0
        finally:
            conn.close()

    async def _fetch_my_handles(self) -> set[str]:
        """
        从数据库读取本机 handle 列表。

        用于过滤自己发送的消息。

        Returns:
            handle 字符串集合
        """
        temp_db = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                temp_db = tmp.name

            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                self._query_my_handles,
                temp_db,
            )
        finally:
            if temp_db and os.path.exists(temp_db):
                try:
                    os.unlink(temp_db)
                except Exception:
                    pass

    def _query_my_handles(self, temp_db: str) -> set[str]:
        """同步查询本机 handle。"""
        shutil.copy2(str(self.CHAT_DB_PATH), temp_db)
        conn = sqlite3.connect(temp_db)
        try:
            # 查找 is_from_me = 1 的消息对应的 handle
            cursor = conn.execute(
                """
                SELECT DISTINCT h.id
                FROM message m
                JOIN handle h ON m.handle_id = h.ROWID
                WHERE m.is_from_me = 1
                LIMIT 10
                """
            )
            return {row[0] for row in cursor.fetchall() if row[0]}
        finally:
            conn.close()


# ── 辅助函数 ─────────────────────────────────────────────────────────────────


def _iso_now() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()
