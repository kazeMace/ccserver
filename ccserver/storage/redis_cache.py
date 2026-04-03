"""
storage.redis_cache — Redis 消息热缓存。

每个 session 对应一个 Redis List，存最近 N 条消息的 JSON 字符串。
Redis 失联时所有方法静默降级，不影响主流程。
"""

import json

from loguru import logger

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None


class RedisMessageCache:

    def __init__(self, redis_url: str, max_size: int, ttl: int):
        """
        redis_url: Redis 连接串，如 redis://localhost:6379
        max_size:  每个 session 缓存的最大消息条数
        ttl:       缓存过期时间（秒）
        """
        self.max_size = max_size
        self.ttl = ttl
        self._client = None
        if aioredis is None:
            logger.warning("RedisCache: redis 包未安装，缓存禁用")
            return
        try:
            self._client = aioredis.from_url(redis_url, decode_responses=True)
        except Exception as exc:
            logger.warning("RedisCache: 初始化失败，缓存禁用 | {}", exc)

    def _key(self, session_id: str) -> str:
        return f"ccserver:session:{session_id}:messages"

    async def push(self, session_id: str, message: dict) -> None:
        """追加一条消息到缓存，超出 max_size 时裁剪旧消息，并刷新 TTL。"""
        if self._client is None:
            return
        try:
            key = self._key(session_id)
            payload = json.dumps(message, default=str)
            await self._client.rpush(key, payload)
            await self._client.ltrim(key, -self.max_size, -1)
            await self._client.expire(key, self.ttl)
        except Exception as exc:
            logger.debug("RedisCache: push 失败，已降级 | session={} err={}", session_id[:8], exc)

    async def get_all(self, session_id: str) -> list | None:
        """
        返回缓存中的所有消息列表，同时刷新 TTL。
        未命中（空列表）或失联时返回 None，触发调用方降级读 MongoDB。
        """
        if self._client is None:
            return None
        try:
            key = self._key(session_id)
            data = await self._client.lrange(key, 0, -1)
            if not data:
                return None
            await self._client.expire(key, self.ttl)
            return [json.loads(item) for item in data]
        except Exception as exc:
            logger.debug("RedisCache: get_all 失败，已降级 | session={} err={}", session_id[:8], exc)
            return None

    async def delete(self, session_id: str) -> None:
        """删除 session 的缓存 Key（压缩后调用）。"""
        if self._client is None:
            return
        try:
            await self._client.delete(self._key(session_id))
        except Exception as exc:
            logger.debug("RedisCache: delete 失败，已降级 | session={} err={}", session_id[:8], exc)

    async def backfill(self, session_id: str, messages: list) -> None:
        """
        回填消息到缓存（仅取最近 max_size 条，防止大 session 内存压力）。
        先 delete 再 push，确保缓存内容与 messages 一致。
        """
        if self._client is None:
            return
        await self.delete(session_id)
        recent = messages[-self.max_size:]
        if not recent:
            return
        try:
            key = self._key(session_id)
            payloads = [json.dumps(msg, default=str) for msg in recent]
            await self._client.rpush(key, *payloads)
            await self._client.expire(key, self.ttl)
        except Exception as exc:
            logger.debug("RedisCache: backfill 失败，已降级 | session={} err={}", session_id[:8], exc)

    async def close(self) -> None:
        """关闭连接（FastAPI lifespan shutdown 时调用）。"""
        if self._client is None:
            return
        try:
            await self._client.aclose()
        except Exception:
            pass
