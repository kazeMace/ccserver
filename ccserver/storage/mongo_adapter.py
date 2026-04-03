"""
storage.mongo_adapter — MongoDB 存储后端。

集合结构：
  sessions      — session 元数据 + msg_seq 计数器
  conversations — 每次 HTTP 请求的轮次（独立集合，避免嵌入数组膨胀）
  messages      — append-only 消息，is_active=False 表示已被压缩替换
  transcripts   — 压缩前的完整快照（仅归档，不参与主流程）
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING, ReturnDocument

from .base import StorageAdapter, SessionRecord


class MongoStorageAdapter(StorageAdapter):

    def __init__(self, mongo_uri: str, db_name: str):
        self._client = AsyncIOMotorClient(mongo_uri)
        self._db = self._client[db_name]
        self._sessions = self._db["sessions"]
        self._conversations = self._db["conversations"]
        self._messages = self._db["messages"]
        self._transcripts = self._db["transcripts"]
        logger.debug("MongoAdapter: 初始化 | db={}", db_name)

    # ── 初始化索引（异步，需在事件循环中调用一次）─────────────────────────────

    async def init_indexes(self) -> None:
        await self._sessions.create_index([("updated_at", DESCENDING)])
        await self._messages.create_index(
            [("session_id", ASCENDING), ("is_active", ASCENDING), ("seq", ASCENDING)]
        )
        await self._messages.create_index(
            [("session_id", ASCENDING), ("conversation_id", ASCENDING)]
        )
        await self._conversations.create_index([("session_id", ASCENDING)])
        logger.debug("MongoAdapter: 索引已创建")

    async def ping(self) -> None:
        """检查 MongoDB 连通性（启动时调用）。"""
        await self._db.command("ping")
        logger.debug("MongoAdapter: ping OK")

    async def close(self) -> None:
        self._client.close()

    # ── get_workdir（MongoDB 无本地目录，用 /tmp 兜底）──────────────────────────

    def get_workdir(self, session_id: str) -> Path:
        workdir = Path(f"/tmp/ccserver/{session_id}/workdir")
        workdir.mkdir(parents=True, exist_ok=True)
        return workdir

    # ── session 生命周期 ───────────────────────────────────────────────────────

    async def create_session(self, record: SessionRecord) -> None:
        doc = {
            "_id": record.session_id,
            "workdir": record.workdir,
            "project_root": record.project_root,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
            "msg_seq": 0,
        }
        await self._sessions.insert_one(doc)
        logger.debug("MongoAdapter: session created | id={}", record.session_id[:8])

    async def load_session(self, session_id: str) -> SessionRecord | None:
        doc = await self._sessions.find_one({"_id": session_id})
        if doc is None:
            return None

        msg_docs = await self._messages.find(
            {"session_id": session_id, "is_active": True},
            sort=[("seq", ASCENDING)],
        ).to_list(length=None)

        messages = [
            {"role": m["role"], "content": m["content"]}
            for m in msg_docs
        ]
        return SessionRecord(
            session_id=session_id,
            workdir=doc["workdir"],
            project_root=doc["project_root"],
            created_at=datetime.fromisoformat(doc["created_at"]),
            updated_at=datetime.fromisoformat(doc["updated_at"]),
            messages=messages,
        )

    async def list_sessions(self) -> list[dict]:
        docs = await self._sessions.find(
            {}, sort=[("updated_at", DESCENDING)]
        ).to_list(length=None)
        result = []
        for doc in docs:
            result.append({
                "id": doc["_id"],
                "workdir": doc["workdir"],
                "project_root": doc["project_root"],
                "created_at": doc["created_at"],
                "updated_at": doc["updated_at"],
            })
        return result

    # ── 消息 IO ───────────────────────────────────────────────────────────────

    async def append_message(self, session_id: str, message: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conv_id = self._current_conv.get(session_id, session_id)

        # 原子自增 msg_seq，取自增后的值作为 seq（第一条消息 seq=1）
        updated = await self._sessions.find_one_and_update(
            {"_id": session_id},
            {"$inc": {"msg_seq": 1}, "$set": {"updated_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        seq = updated["msg_seq"]

        await self._messages.insert_one({
            "session_id": session_id,
            "conversation_id": conv_id,
            "role": message["role"],
            "content": message["content"],   # 直接存 BSON，不 JSON 序列化
            "is_active": True,
            "seq": seq,
            "created_at": now,
        })

    async def rewrite_messages(self, session_id: str, messages: list) -> None:
        """
        崩溃安全顺序：先软删除旧消息，再插入新消息。
        最坏情况（崩溃在步骤 1 后）：消息丢失（可接受，压缩前已归档至 transcripts）。
        """
        now = datetime.now(timezone.utc).isoformat()
        conv_id = self._current_conv.get(session_id, session_id)

        # 步骤 1：软删除旧消息
        await self._messages.update_many(
            {"session_id": session_id, "is_active": True},
            {"$set": {"is_active": False}},
        )

        # 步骤 2：获取当前 msg_seq 基准，批量插入新消息
        doc = await self._sessions.find_one({"_id": session_id})
        base_seq = doc["msg_seq"] if doc else 0

        new_docs = []
        for i, msg in enumerate(messages):
            new_docs.append({
                "session_id": session_id,
                "conversation_id": conv_id,
                "role": msg["role"],
                "content": msg["content"],
                "is_active": True,
                "seq": base_seq + i + 1,
                "created_at": now,
            })

        if new_docs:
            await self._messages.insert_many(new_docs)

        new_seq = base_seq + len(messages)
        await self._sessions.update_one(
            {"_id": session_id},
            {"$set": {"msg_seq": new_seq, "updated_at": now}},
        )
        logger.debug(
            "MongoAdapter: messages rewritten | id={} new_count={}",
            session_id[:8], len(messages)
        )

    async def save_transcript(self, session_id: str, messages: list) -> str:
        now = datetime.now(timezone.utc).isoformat()
        conv_id = self._current_conv.get(session_id, session_id)
        result = await self._transcripts.insert_one({
            "session_id": session_id,
            "conversation_id": conv_id,
            "messages": messages,
            "created_at": now,
        })
        transcript_id = str(result.inserted_id)
        logger.debug("MongoAdapter: transcript saved | id={} tid={}", session_id[:8], transcript_id)
        return f"transcript:{transcript_id}"

    async def update_meta(self, session_id: str, updated_at: datetime) -> None:
        await self._sessions.update_one(
            {"_id": session_id},
            {"$set": {"updated_at": updated_at.isoformat()}},
        )

    # ── conversation 管理 ─────────────────────────────────────────────────────

    # session_id → 当前活跃 conversation_id 的内存映射
    _current_conv: dict[str, str] = {}

    async def create_conversation(self, session_id: str, conversation_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._conversations.insert_one({
            "_id": conversation_id,
            "session_id": session_id,
            "created_at": now,
        })
        self._current_conv[session_id] = conversation_id
        logger.debug(
            "MongoAdapter: conversation created | session={} conv={}",
            session_id[:8], conversation_id[:8]
        )
