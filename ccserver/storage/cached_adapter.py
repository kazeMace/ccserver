"""
storage.cached_adapter — 通用缓存包装层。

CachedStorageAdapter 包裹任意 StorageAdapter，拦截三个方法注入 Redis 缓存逻辑：
  - load_session:    元数据读 inner；消息优先读 Redis，miss 时读 inner 并回填
  - append_message:  写 inner 后追加到 Redis
  - rewrite_messages: 写 inner 后删除旧缓存并回填

其余方法直接代理给 inner，与缓存无关。
"""

from datetime import datetime
from pathlib import Path

from loguru import logger

from .base import StorageAdapter, SessionRecord
from .redis_cache import RedisMessageCache


class CachedStorageAdapter(StorageAdapter):

    def __init__(self, inner: StorageAdapter, cache: RedisMessageCache):
        self._inner = inner
        self._cache = cache

    # ── 直接代理 ──────────────────────────────────────────────────────────────

    def get_workdir(self, session_id: str) -> Path:
        return self._inner.get_workdir(session_id)

    async def create_session(self, record: SessionRecord) -> None:
        await self._inner.create_session(record)

    async def list_sessions(self) -> list[dict]:
        return await self._inner.list_sessions()

    async def save_transcript(self, session_id: str, messages: list) -> str:
        return await self._inner.save_transcript(session_id, messages)

    async def update_meta(self, session_id: str, updated_at: datetime) -> None:
        await self._inner.update_meta(session_id, updated_at)

    async def create_conversation(self, session_id: str, conversation_id: str) -> None:
        await self._inner.create_conversation(session_id, conversation_id)

    # ── Team 存储代理 ──────────────────────────────────────────────────────────

    async def save_team(self, team_data: dict) -> None:
        await self._inner.save_team(team_data)

    async def load_team(self, team_name: str) -> dict | None:
        return await self._inner.load_team(team_name)

    async def delete_team(self, team_name: str) -> None:
        await self._inner.delete_team(team_name)

    async def list_teams(self) -> list[dict]:
        return await self._inner.list_teams()

    # ── Mailbox 存储代理 ───────────────────────────────────────────────────────

    async def append_inbox_message(self, team_name: str, recipient: str, message: dict) -> None:
        await self._inner.append_inbox_message(team_name, recipient, message)

    async def fetch_inbox_messages(
        self,
        team_name: str,
        recipient: str,
        unread_only: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        return await self._inner.fetch_inbox_messages(team_name, recipient, unread_only, limit)

    async def mark_inbox_read(self, team_name: str, recipient: str, message_ids: list[str]) -> None:
        await self._inner.mark_inbox_read(team_name, recipient, message_ids)

    # ── 带缓存逻辑的方法 ──────────────────────────────────────────────────────

    async def load_session(self, session_id: str) -> SessionRecord | None:
        # 先从 inner 加载 session 元数据（workdir、project_root 等）
        record = await self._inner.load_session(session_id)
        if record is None:
            return None

        # 消息部分：优先从 Redis 读
        cached_messages = await self._cache.get_all(session_id)
        if cached_messages is not None:
            logger.debug("CachedAdapter: cache hit | session={} count={}", session_id[:8], len(cached_messages))
            record.messages = cached_messages
        else:
            # cache miss：用 inner 已加载的消息回填缓存
            logger.debug("CachedAdapter: cache miss | session={} count={}", session_id[:8], len(record.messages))
            await self._cache.backfill(session_id, record.messages)

        return record

    async def append_message(self, session_id: str, message: dict) -> None:
        await self._inner.append_message(session_id, message)
        await self._cache.push(session_id, message)

    async def rewrite_messages(self, session_id: str, messages: list) -> None:
        await self._inner.rewrite_messages(session_id, messages)
        # 清除旧缓存，回填新内容（仅最近 max_size 条）
        await self._cache.backfill(session_id, messages)

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    async def ping(self) -> None:
        """检查 inner 连通性（Redis 不强制 ping，失联时降级）。"""
        if hasattr(self._inner, "ping"):
            await self._inner.ping()

    async def close(self) -> None:
        if hasattr(self._inner, "close"):
            await self._inner.close()
        await self._cache.close()
