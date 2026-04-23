# src/storage/sqlite_adapter.py
"""
SQLite 存储后端。

表结构：
  sessions       — session 元数据
  conversations  — 每次 HTTP 请求的轮次（关联 session）
  messages       — append-only 消息，is_active=0 表示已被压缩替换
  transcripts    — 压缩前的完整快照（仅归档，不参与主流程）
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from .base import StorageAdapter, SessionRecord


class SQLiteStorageAdapter(StorageAdapter):

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── 数据库初始化 ───────────────────────────────────────────────────────────

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id   TEXT PRIMARY KEY,
                    workdir      TEXT NOT NULL,
                    project_root TEXT NOT NULL,
                    created_at   TEXT NOT NULL,
                    updated_at   TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    session_id      TEXT NOT NULL,
                    created_at      TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id      TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    role            TEXT NOT NULL,
                    content         TEXT NOT NULL,   -- JSON 序列化
                    is_active       INTEGER NOT NULL DEFAULT 1,  -- 1=有效, 0=已压缩
                    created_at      TEXT NOT NULL,
                    FOREIGN KEY (session_id)      REFERENCES sessions(session_id),
                    FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id)
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages(session_id, is_active);

                CREATE TABLE IF NOT EXISTS transcripts (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id      TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    messages_json   TEXT NOT NULL,   -- 压缩前完整消息的 JSON 数组
                    created_at      TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id              TEXT NOT NULL,
                    session_id      TEXT NOT NULL,
                    subject         TEXT NOT NULL,
                    description     TEXT NOT NULL,
                    status          TEXT NOT NULL DEFAULT 'pending',
                    task_type       TEXT NOT NULL DEFAULT 'general',
                    agent_id        TEXT,
                    agent_type      TEXT,
                    blocked_by      TEXT,   -- JSON array as text
                    blocks          TEXT,   -- JSON array as text
                    started_at      TEXT,
                    completed_at    TEXT,
                    output_summary  TEXT,
                    output_data     TEXT,   -- JSON as text
                    PRIMARY KEY (session_id, id)
                );

                CREATE TABLE IF NOT EXISTS task_counter (
                    session_id      TEXT PRIMARY KEY,
                    counter         INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS teams (
                    name            TEXT PRIMARY KEY,
                    data            TEXT NOT NULL    -- JSON 序列化的团队数据
                );

                CREATE TABLE IF NOT EXISTS inbox_messages (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    team_name       TEXT NOT NULL,
                    recipient       TEXT NOT NULL,   -- 收件人 agent_id
                    message_json    TEXT NOT NULL,   -- JSON 序列化的消息体
                    created_at      TEXT NOT NULL,
                    read            INTEGER NOT NULL DEFAULT 0  -- 0=未读, 1=已读
                );

                CREATE INDEX IF NOT EXISTS idx_inbox_team_recipient
                    ON inbox_messages(team_name, recipient);

                CREATE INDEX IF NOT EXISTS idx_inbox_read
                    ON inbox_messages(team_name, recipient, read);
            """)
        logger.debug("SQLiteAdapter: db initialized | path={}", self.db_path)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── session 生命周期 ───────────────────────────────────────────────────────

    def get_workdir(self, session_id: str) -> Path:
        return Path(f"/tmp/ccserver/{session_id}/workdir")

    def create_session(self, record: SessionRecord) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO sessions (session_id, workdir, project_root, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.session_id,
                    record.workdir,
                    record.project_root,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
        logger.debug("SQLiteAdapter: session created | id={}", record.session_id[:8])

    def load_session(self, session_id: str) -> SessionRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None

            # 只加载 is_active=1 的消息作为当前有效历史
            msg_rows = conn.execute(
                """
                SELECT role, content FROM messages
                WHERE session_id = ? AND is_active = 1
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()

        messages = [
            {"role": r["role"], "content": json.loads(r["content"])}
            for r in msg_rows
        ]
        return SessionRecord(
            session_id=session_id,
            workdir=row["workdir"],
            project_root=row["project_root"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            messages=messages,
        )

    def list_sessions(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        result = []
        for r in rows:
            row_dict = dict(r)
            # 统一字段名：sqlite 列名是 session_id，对外统一用 id（与 file_adapter 和 to_meta() 一致）
            row_dict["id"] = row_dict.pop("session_id")
            result.append(row_dict)
        return result

    # ── 消息 IO ───────────────────────────────────────────────────────────────

    def append_message(self, session_id: str, message: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        # conversation_id 通过 set_conversation 提前设置，找不到时用 session_id 兜底
        conv_id = self._current_conv.get(session_id, session_id)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO messages (session_id, conversation_id, role, content, is_active, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
                """,
                (
                    session_id,
                    conv_id,
                    message["role"],
                    json.dumps(message["content"], default=str),
                    now,
                ),
            )

    def rewrite_messages(self, session_id: str, messages: list) -> None:
        """
        方案 B：不物理删除，将所有 is_active=1 的消息标记为 is_active=0，
        再插入压缩后的摘要消息（is_active=1）。
        """
        now = datetime.now(timezone.utc).isoformat()
        conv_id = self._current_conv.get(session_id, session_id)
        with self._conn() as conn:
            # 标记旧消息为已压缩
            conn.execute(
                "UPDATE messages SET is_active = 0 WHERE session_id = ? AND is_active = 1",
                (session_id,),
            )
            # 插入压缩后的摘要消息
            for msg in messages:
                conn.execute(
                    """
                    INSERT INTO messages (session_id, conversation_id, role, content, is_active, created_at)
                    VALUES (?, ?, ?, ?, 1, ?)
                    """,
                    (
                        session_id,
                        conv_id,
                        msg["role"],
                        json.dumps(msg["content"], default=str),
                        now,
                    ),
                )
        logger.debug(
            "SQLiteAdapter: messages rewritten (soft) | id={} new_count={}",
            session_id[:8], len(messages)
        )

    def save_transcript(self, session_id: str, messages: list) -> str:
        """归档压缩前的完整消息到 transcripts 表，返回记录 ID。"""
        now = datetime.now(timezone.utc).isoformat()
        conv_id = self._current_conv.get(session_id, session_id)
        with self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO transcripts (session_id, conversation_id, messages_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, conv_id, json.dumps(messages, default=str), now),
            )
            transcript_id = cursor.lastrowid
        logger.debug("SQLiteAdapter: transcript saved | id={} transcript_id={}", session_id[:8], transcript_id)
        return f"transcript:{transcript_id}"

    def update_meta(self, session_id: str, updated_at: datetime) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
                (updated_at.isoformat(), session_id),
            )

    # ── conversation 管理（SQLite 特有）──────────────────────────────────────

    # 当前 conversation_id 的内存映射：session_id → conversation_id
    # 每次 HTTP 请求开始时通过 set_conversation() 设置
    _current_conv: dict[str, str] = {}

    def create_conversation(self, session_id: str, conversation_id: str) -> None:
        """注册一次新的对话轮次，并设为当前活跃 conversation。"""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO conversations (conversation_id, session_id, created_at) VALUES (?, ?, ?)",
                (conversation_id, session_id, now),
            )
        self._current_conv[session_id] = conversation_id
        logger.debug(
            "SQLiteAdapter: conversation created | session={} conv={}",
            session_id[:8], conversation_id[:8]
        )

    def set_conversation(self, session_id: str, conversation_id: str) -> None:
        """切换当前活跃 conversation（不创建新记录）。"""
        self._current_conv[session_id] = conversation_id

    def list_conversations(self, session_id: str) -> list[dict]:
        """列出一个 session 下的所有 conversation，按时间排序。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM conversations WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_full_history(self, session_id: str) -> list[dict]:
        """返回包含已压缩消息在内的完整历史（调试用）。"""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT role, content, is_active, conversation_id, created_at
                FROM messages WHERE session_id = ? ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()
        return [
            {
                "role": r["role"],
                "content": json.loads(r["content"]),
                "is_active": bool(r["is_active"]),
                "conversation_id": r["conversation_id"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    # ── Task 存储 ─────────────────────────────────────────────────────────────

    def create_task(self, session_id: str, task_data: dict) -> None:
        """创建任务记录。"""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO tasks (id, session_id, subject, description, status, task_type, agent_id, agent_type, blocked_by, blocks, started_at, completed_at, output_summary, output_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_data["id"],
                    session_id,
                    task_data["subject"],
                    task_data["description"],
                    task_data.get("status", "pending"),
                    task_data.get("task_type", "general"),
                    task_data.get("agent_id"),
                    task_data.get("agent_type"),
                    json.dumps(task_data.get("blocked_by", [])),
                    json.dumps(task_data.get("blocks", [])),
                    task_data.get("started_at"),
                    task_data.get("completed_at"),
                    task_data.get("output_summary"),
                    json.dumps(task_data.get("output_data")) if task_data.get("output_data") else None,
                ),
            )

    def load_task(self, session_id: str, task_id: str) -> dict | None:
        """加载任务。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE session_id = ? AND id = ?",
                (session_id, task_id),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_task(dict(row))

    def update_task(self, session_id: str, task_data: dict) -> None:
        """更新任务（覆盖式）。"""
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE tasks SET subject=?, description=?, status=?, task_type=?, agent_id=?, agent_type=?, blocked_by=?, blocks=?, started_at=?, completed_at=?, output_summary=?, output_data=?
                WHERE session_id = ? AND id = ?
                """,
                (
                    task_data["subject"],
                    task_data["description"],
                    task_data.get("status", "pending"),
                    task_data.get("task_type", "general"),
                    task_data.get("agent_id"),
                    task_data.get("agent_type"),
                    json.dumps(task_data.get("blocked_by", [])),
                    json.dumps(task_data.get("blocks", [])),
                    task_data.get("started_at"),
                    task_data.get("completed_at"),
                    task_data.get("output_summary"),
                    json.dumps(task_data.get("output_data")) if task_data.get("output_data") else None,
                    session_id,
                    task_data["id"],
                ),
            )

    def list_tasks(self, session_id: str) -> list[dict]:
        """列出所有任务。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        return [self._row_to_task(dict(r)) for r in rows]

    def get_task_counter(self, session_id: str) -> int:
        """获取任务自增计数器。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT counter FROM task_counter WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return row["counter"] if row else 0

    def set_task_counter(self, session_id: str, value: int) -> None:
        """设置任务自增计数器。"""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO task_counter (session_id, counter) VALUES (?, ?)",
                (session_id, value),
            )

    def _row_to_task(self, row: dict) -> dict:
        """将数据库行转换为任务字典。"""
        return {
            "id": row["id"],
            "subject": row["subject"],
            "description": row["description"],
            "status": row["status"],
            "task_type": row["task_type"],
            "agent_id": row["agent_id"],
            "agent_type": row["agent_type"],
            "blocked_by": json.loads(row["blocked_by"]) if row["blocked_by"] else [],
            "blocks": json.loads(row["blocks"]) if row["blocks"] else [],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "output_summary": row["output_summary"],
            "output_data": json.loads(row["output_data"]) if row["output_data"] else None,
        }

    # ── Team 存储 ──────────────────────────────────────────────────────────────

    def save_team(self, team_data: dict) -> None:
        """插入或更新 teams 表。"""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO teams (name, data) VALUES (?, ?)",
                (team_data["name"], json.dumps(team_data, ensure_ascii=False)),
            )
        logger.debug("SQLiteAdapter: team saved | name={}", team_data["name"])

    def load_team(self, team_name: str) -> dict | None:
        """按名称加载团队数据。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM teams WHERE name = ?", (team_name,)
            ).fetchone()
            if row is None:
                return None
            return json.loads(row["data"])

    def delete_team(self, team_name: str) -> None:
        """删除团队及其关联的 inbox 消息。"""
        with self._conn() as conn:
            conn.execute("DELETE FROM teams WHERE name = ?", (team_name,))
            conn.execute("DELETE FROM inbox_messages WHERE team_name = ?", (team_name,))
        logger.debug("SQLiteAdapter: team deleted | name={}", team_name)

    def list_teams(self) -> list[dict]:
        """列出所有团队数据。"""
        with self._conn() as conn:
            rows = conn.execute("SELECT data FROM teams ORDER BY name ASC").fetchall()
        return [json.loads(r["data"]) for r in rows]

    # ── Mailbox 存储 ───────────────────────────────────────────────────────────

    def append_inbox_message(self, team_name: str, recipient: str, message: dict) -> None:
        """向 inbox_messages 表插入一条消息。"""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO inbox_messages (team_name, recipient, message_json, created_at, read)
                VALUES (?, ?, ?, ?, 0)
                """,
                (team_name, recipient, json.dumps(message, default=str), now),
            )
        logger.debug(
            "SQLiteAdapter: inbox appended | team={} recipient={} msg_id={}",
            team_name,
            recipient,
            message.get("id", "?"),
        )

    def fetch_inbox_messages(
        self,
        team_name: str,
        recipient: str,
        unread_only: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        """查询 inbox 消息列表。"""
        sql = """
            SELECT message_json FROM inbox_messages
            WHERE team_name = ? AND recipient = ?
        """
        params = [team_name, recipient]
        if unread_only:
            sql += " AND read = 0"
        sql += " ORDER BY id ASC"
        if limit > 0:
            sql += f" LIMIT {limit}"

        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()

        messages = []
        for r in rows:
            msg = json.loads(r["message_json"])
            messages.append(msg)
        return messages

    def mark_inbox_read(self, team_name: str, recipient: str, message_ids: list[str]) -> None:
        """
        将指定消息标记为已读。
        由于消息体以 JSON 存储在 message_json 中，
        我们通过遍历并匹配 message.id 来更新对应记录。
        """
        if not message_ids:
            return

        target_ids = set(message_ids)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, message_json FROM inbox_messages WHERE team_name = ? AND recipient = ?",
                (team_name, recipient),
            ).fetchall()

            updated = 0
            for r in rows:
                msg = json.loads(r["message_json"])
                if msg.get("id") in target_ids and not msg.get("read"):
                    msg["read"] = True
                    conn.execute(
                        "UPDATE inbox_messages SET message_json = ?, read = 1 WHERE id = ?",
                        (json.dumps(msg, default=str), r["id"]),
                    )
                    updated += 1

        logger.debug(
            "SQLiteAdapter: inbox marked read | team={} recipient={} updated={}",
            team_name,
            recipient,
            updated,
        )
