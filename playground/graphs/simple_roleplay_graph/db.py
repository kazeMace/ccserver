"""
db — SQLite 对话历史存储。

表结构：
    sessions (session_id TEXT PRIMARY KEY, persona_id TEXT, created_at TEXT)
    messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,
              content TEXT, created_at TEXT)

只保存通过质量检测的 response（由 graph.py 的 save_turn 在 passed=True 后调用）。
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "simple_roleplay_graph.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """建表（幂等）。"""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id  TEXT PRIMARY KEY,
                persona_id  TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)")


def create_session(session_id: str, persona_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sessions(session_id, persona_id, created_at) VALUES(?,?,?)",
            (session_id, persona_id, _now()),
        )


def session_exists(session_id: str) -> bool:
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone()
    return row is not None


def save_turn(session_id: str, user_msg: str, assistant_msg: str) -> None:
    now = _now()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO messages(session_id, role, content, created_at) VALUES(?,?,?,?)",
            (session_id, "user", user_msg, now),
        )
        conn.execute(
            "INSERT INTO messages(session_id, role, content, created_at) VALUES(?,?,?,?)",
            (session_id, "assistant", assistant_msg, now),
        )


def get_history_list(session_id: str, k: int = 10) -> list[dict]:
    """返回最近 k 轮（user+assistant 各一条为一轮）的消息列表。"""
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT role, content FROM messages
            WHERE session_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, k * 2),
        ).fetchall()
    # 查出来是倒序，翻转回正序
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# 模块加载时自动建表
init_db()
