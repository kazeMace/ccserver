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
        self._tasks = self._db["tasks"]
        self._teams = self._db["teams"]
        self._inbox_messages = self._db["inbox_messages"]
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
        await self._inbox_messages.create_index(
            [("team_name", ASCENDING), ("recipient", ASCENDING), ("read", ASCENDING)]
        )
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

    # ── Task 存储 ─────────────────────────────────────────────────────────────

    async def create_task(self, session_id: str, task_data: dict) -> None:
        """在 _tasks 集合中插入任务文档。"""
        doc = dict(task_data)
        doc["session_id"] = session_id
        await self._tasks.insert_one(doc)
        logger.debug(
            "MongoAdapter: task created | session={} task_id={}",
            session_id[:8], task_data.get("id")
        )

    async def load_task(self, session_id: str, task_id: str) -> dict | None:
        """按 session_id 与 id 查询任务。"""
        doc = await self._tasks.find_one({"session_id": session_id, "id": task_id})
        if doc is None:
            return None
        doc.pop("_id", None)
        doc.pop("session_id", None)
        return doc

    async def update_task(self, session_id: str, task_data: dict) -> None:
        """覆盖更新任务文档。"""
        await self._tasks.replace_one(
            {"session_id": session_id, "id": task_data["id"]},
            {**task_data, "session_id": session_id},
            upsert=True,
        )
        logger.debug(
            "MongoAdapter: task updated | session={} task_id={}",
            session_id[:8], task_data.get("id")
        )

    async def list_tasks(self, session_id: str) -> list[dict]:
        """列出某 session 下所有任务，按 id 升序。"""
        docs = await self._tasks.find({"session_id": session_id}).to_list(length=None)
        for doc in docs:
            doc.pop("_id", None)
            doc.pop("session_id", None)
        return sorted(docs, key=lambda t: int(t.get("id", "0")))

    async def get_task_counter(self, session_id: str) -> int:
        """从 sessions 集合读取 task_counter 字段。"""
        doc = await self._sessions.find_one({"_id": session_id})
        return doc.get("task_counter", 0) if doc else 0

    async def set_task_counter(self, session_id: str, value: int) -> None:
        """更新 sessions 集合中的 task_counter 字段。"""
        await self._sessions.update_one(
            {"_id": session_id},
            {"$set": {"task_counter": value}},
            upsert=True,
        )

    # ── Team 存储 ──────────────────────────────────────────────────────────────

    async def save_team(self, team_data: dict) -> None:
        """插入或替换 teams 集合中的团队文档。"""
        doc = dict(team_data)
        doc["_id"] = team_data["name"]
        await self._teams.replace_one(
            {"_id": team_data["name"]},
            doc,
            upsert=True,
        )
        logger.debug("MongoAdapter: team saved | name={}", team_data["name"])

    async def load_team(self, team_name: str) -> dict | None:
        """按名称加载团队数据。"""
        doc = await self._teams.find_one({"_id": team_name})
        if doc is None:
            return None
        doc.pop("_id", None)
        return doc

    async def delete_team(self, team_name: str) -> None:
        """删除团队及其关联的 inbox 消息。"""
        await self._teams.delete_one({"_id": team_name})
        await self._inbox_messages.delete_many({"team_name": team_name})
        logger.debug("MongoAdapter: team deleted | name={}", team_name)

    async def list_teams(self) -> list[dict]:
        """列出所有团队数据，按名称升序。"""
        docs = await self._teams.find({}).to_list(length=None)
        for doc in docs:
            doc.pop("_id", None)
        return sorted(docs, key=lambda t: t.get("name", ""))

    # ── Mailbox 存储 ───────────────────────────────────────────────────────────

    async def append_inbox_message(self, team_name: str, recipient: str, message: dict) -> None:
        """向 inbox_messages 集合插入一条消息。"""
        now = datetime.now(timezone.utc).isoformat()
        doc = {
            "team_name": team_name,
            "recipient": recipient,
            "message": message,
            "created_at": now,
            "read": False,
        }
        await self._inbox_messages.insert_one(doc)
        logger.debug(
            "MongoAdapter: inbox appended | team={} recipient={} msg_id={}",
            team_name,
            recipient,
            message.get("id", "?"),
        )

    async def fetch_inbox_messages(
        self,
        team_name: str,
        recipient: str,
        unread_only: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        """查询 inbox 消息列表。"""
        query: dict = {"team_name": team_name, "recipient": recipient}
        if unread_only:
            query["read"] = False

        cursor = self._inbox_messages.find(query).sort("created_at", ASCENDING)
        if limit > 0:
            cursor = cursor.limit(limit)

        docs = await cursor.to_list(length=None)
        messages = []
        for doc in docs:
            msg = doc.get("message", {})
            msg.setdefault("read", doc.get("read", False))
            messages.append(msg)
        return messages

    async def mark_inbox_read(self, team_name: str, recipient: str, message_ids: list[str]) -> None:
        """将指定消息标记为已读。"""
        if not message_ids:
            return

        # 由于消息体嵌套在 message 字段中，我们通过遍历更新
        target_ids = set(message_ids)
        docs = await self._inbox_messages.find(
            {"team_name": team_name, "recipient": recipient}
        ).to_list(length=None)

        updated = 0
        for doc in docs:
            msg = doc.get("message", {})
            if msg.get("id") in target_ids and not doc.get("read"):
                await self._inbox_messages.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"read": True}},
                )
                updated += 1

        logger.debug(
            "MongoAdapter: inbox marked read | team={} recipient={} updated={}",
            team_name,
            recipient,
            updated,
        )
