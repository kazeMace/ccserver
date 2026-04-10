#!/usr/bin/env python3
"""
Memory MCP Server

Provides persistent memory and user profile management via SQLite.

Tools:
  - save_memory(content, metadata)       → store a memory
  - retrieve_memory(query, limit)        → keyword search memories
  - list_memories(limit)                 → list recent memories
  - delete_memory(memory_id)             → remove a memory
  - update_user_profile(slot_name, value) → set a profile attribute
  - get_user_profile()                   → read full profile
  - clear_user_profile_slot(slot_name)   → remove one profile attribute
"""

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_SERVER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SERVER_DIR.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "memory.db"
PROFILE_PATH = DATA_DIR / "user_profile.json"

mcp = FastMCP("memory")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _get_db() -> sqlite3.Connection:
    _ensure_data_dir()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            content     TEXT    NOT NULL,
            metadata    TEXT    DEFAULT '{}',
            created_at  TEXT    NOT NULL
        )
    """)
    conn.commit()
    return conn


def _load_profile() -> dict:
    if PROFILE_PATH.exists():
        with open(PROFILE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_profile(profile: dict) -> None:
    _ensure_data_dir()
    with open(PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Memory tools
# ---------------------------------------------------------------------------

@mcp.tool()
def save_memory(content: str, metadata: str = "{}") -> str:
    """
    将一条信息持久化保存到记忆库。
    适用于：对话摘要、重要事件、用户透露的细节等非结构化内容。

    参数：
        content:  要保存的文本内容。
                  例如："用户提到不喜欢吃辣"、"用户正在准备考研"
        metadata: 可选的 JSON 字符串，用于添加分类标签。
                  例如：'{"category": "preference"}' 或 '{"category": "conversation_summary", "rounds": "1-10"}'

    返回：
        包含新记忆 ID 的确认信息。
    """
    try:
        meta = json.loads(metadata) if metadata.strip() else {}
    except json.JSONDecodeError:
        meta = {"raw_metadata": metadata}

    conn = _get_db()
    cursor = conn.execute(
        "INSERT INTO memories (content, metadata, created_at) VALUES (?, ?, ?)",
        (content, json.dumps(meta, ensure_ascii=False), datetime.now().isoformat()),
    )
    conn.commit()
    memory_id = cursor.lastrowid
    conn.close()
    return f"Memory #{memory_id} saved."


@mcp.tool()
def retrieve_memory(query: str, limit: int = 5) -> str:
    """
    通过关键词搜索记忆库，返回相关记忆。

    参数：
        query: 搜索关键词，多个词用空格分隔，任意词命中即返回。
        limit: 最多返回条数（默认 5）。

    返回：
        按时间倒序排列的匹配记忆列表。
    """
    words = [w for w in query.lower().split() if w]
    if not words:
        return "Please provide a search query."

    conn = _get_db()
    conditions = " OR ".join(["LOWER(content) LIKE ?" for _ in words])
    params = [f"%{w}%" for w in words] + [limit]
    rows = conn.execute(
        f"SELECT id, content, metadata, created_at FROM memories "
        f"WHERE {conditions} ORDER BY created_at DESC LIMIT ?",
        params,
    ).fetchall()
    conn.close()

    if not rows:
        return "No matching memories found."

    lines = [f"Found {len(rows)} relevant memories:\n"]
    for row in rows:
        date = row[3][:10]
        lines.append(f"[#{row[0]} | {date}] {row[1]}")
    return "\n".join(lines)


@mcp.tool()
def list_memories(limit: int = 10) -> str:
    """
    列出最近保存的记忆，按时间倒序排列。

    参数：
        limit: 返回条数（默认 10）。

    返回：
        最近的记忆列表，每条包含 ID、日期和内容摘要。
    """
    conn = _get_db()
    rows = conn.execute(
        "SELECT id, content, created_at FROM memories ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()

    if not rows:
        return "No memories stored yet."

    lines = [f"Recent {len(rows)} memories:\n"]
    for row in rows:
        date = row[2][:10]
        snippet = row[1][:120] + ("..." if len(row[1]) > 120 else "")
        lines.append(f"[#{row[0]} | {date}] {snippet}")
    return "\n".join(lines)


@mcp.tool()
def delete_memory(memory_id: int) -> str:
    """
    根据 ID 删除指定记忆。

    参数：
        memory_id: 要删除的记忆 ID（数字）。

    返回：
        删除成功或未找到的确认信息。
    """
    conn = _get_db()
    result = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    conn.commit()
    deleted = result.rowcount
    conn.close()
    return f"Memory #{memory_id} deleted." if deleted else f"Memory #{memory_id} not found."


# ---------------------------------------------------------------------------
# User profile tools
# ---------------------------------------------------------------------------

@mcp.tool()
def update_user_profile(slot_name: str, value: str) -> str:
    """
    新增或更新用户画像中的一个槽位属性。

    参数：
        slot_name: 属性名，使用小写英文下划线格式。
                   例如："age"、"diet_restriction"、"favorite_genre"、"city"
        value:     属性值，简洁中文或英文描述。

    返回：
        更新成功的确认信息。
    """
    profile = _load_profile()
    profile[slot_name] = {
        "value": value,
        "updated_at": datetime.now().isoformat(),
    }
    _save_profile(profile)
    return f"Profile updated: {slot_name} = {value!r}"


@mcp.tool()
def get_user_profile() -> str:
    """
    读取完整的用户画像，返回所有已保存的槽位及其值。

    返回：
        所有画像属性，包含属性名、值和最后更新时间。
    """
    profile = _load_profile()
    if not profile:
        return "No user profile data yet."

    lines = ["User Profile:\n"]
    for key, data in profile.items():
        if isinstance(data, dict):
            val = data.get("value", "")
            updated = data.get("updated_at", "")[:10]
            lines.append(f"  {key}: {val}  (updated {updated})")
        else:
            lines.append(f"  {key}: {data}")
    return "\n".join(lines)


@mcp.tool()
def clear_user_profile_slot(slot_name: str) -> str:
    """
    从用户画像中删除指定槽位。
    适用于：用户明确表示某条信息已不再成立时（如"我已经不减肥了"）。

    参数：
        slot_name: 要删除的属性名。

    返回：
        删除成功或未找到的确认信息。
    """
    profile = _load_profile()
    if slot_name not in profile:
        return f"Slot '{slot_name}' not found in profile."
    del profile[slot_name]
    _save_profile(profile)
    return f"Profile slot '{slot_name}' removed."


if __name__ == "__main__":
    mcp.run()
