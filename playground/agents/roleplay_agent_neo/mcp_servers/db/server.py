#!/usr/bin/env python3
"""
数据库 MCP Server

统一管理所有会话数据，替代原来分散的文件存储。
数据库路径：chat.db（SQLite，项目根目录）

conversation_id 作为显式参数传入每个工具（MCP server 是独立子进程，
无法读取 Claude Code 运行时动态注入的环境变量）。

工具列表：
  Session 管理：
    create_session(conversation_id, persona_name, user_name)
    get_session(conversation_id)
    update_session_user_name(conversation_id, user_name)
    list_sessions(limit, offset)

  消息管理：
    save_message(conversation_id, role, content)
    get_history(conversation_id, k)
    get_latest_user_message(conversation_id)
    get_all_messages(conversation_id)
    count_messages(conversation_id)

  用户画像：
    update_profile(conversation_id, slot_name, value)
    get_profile(conversation_id)
    get_profile_slot(conversation_id, slot_name)
    clear_profile_slot(conversation_id, slot_name)
    clear_all_profile(conversation_id)

  用户记忆：
    add_user_memory(conversation_id, content, memory_date)
    search_user_memory(conversation_id, keywords, limit)
    get_all_user_memory(conversation_id)
    delete_user_memory(conversation_id, memory_id)

  角色新设定：
    add_persona_memory(conversation_id, content, memory_date)
    get_persona_memory(conversation_id)
    search_persona_memory(conversation_id, keywords, limit)
    delete_persona_memory(conversation_id, memory_id)

  对话摘要：
    save_summary(conversation_id, content, rounds_covered)
    get_summary(conversation_id)
    delete_summary(conversation_id)

  Personas（不需要 conversation_id）：
    get_persona(name)
    list_personas()
    get_fewshot(name)
    upsert_persona(name, persona_content, fewshot_content, model)
    delete_persona(name)
"""

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date
from pathlib import Path

from mcp.server.fastmcp import FastMCP

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = _PROJECT_ROOT / "chat.db"

mcp = FastMCP("db")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

@contextmanager
def _db():
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# Session 管理
# ---------------------------------------------------------------------------

@mcp.tool()
def create_session(conversation_id: str, persona_name: str, user_name: str = "用户") -> str:
    """
    创建新会话记录。若同一 conversation_id 已存在则返回已存在提示，不覆盖。

    参数：
        conversation_id: 会话唯一标识，由调用方（api.py）生成并通过 hook 注入上下文。
        persona_name:    角色名称。
        user_name:       用户称呼，默认"用户"。
    """
    with _db() as conn:
        existing = conn.execute(
            "SELECT conversation_id FROM sessions WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
        if existing:
            return f"会话 {conversation_id} 已存在，无需重复创建"
        conn.execute(
            "INSERT INTO sessions (conversation_id, persona_name, user_name, created_at) VALUES (?, ?, ?, ?)",
            (conversation_id, persona_name, user_name, _now()),
        )
    return f"会话已创建：conversation_id={conversation_id}, persona={persona_name}, user={user_name}"


@mcp.tool()
def get_session(conversation_id: str) -> str:
    """
    获取指定会话信息。

    返回：
        JSON：{conversation_id, persona_name, user_name, created_at}；不存在返回 null。
    """
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
    if row is None:
        return "null"
    return json.dumps(dict(row), ensure_ascii=False)


@mcp.tool()
def update_session_user_name(conversation_id: str, user_name: str) -> str:
    """
    更新指定会话的用户称呼。

    参数：
        conversation_id: 会话 ID。
        user_name:       新的用户称呼。
    """
    with _db() as conn:
        conn.execute(
            "UPDATE sessions SET user_name = ? WHERE conversation_id = ?",
            (user_name, conversation_id),
        )
    return f"用户称呼已更新为：{user_name}"


@mcp.tool()
def list_sessions(limit: int = 20, offset: int = 0) -> str:
    """
    列出所有会话记录（按创建时间倒序）。

    返回：
        JSON 数组，每项包含 conversation_id、persona_name、user_name、created_at。
    """
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return json.dumps([dict(r) for r in rows], ensure_ascii=False)


# ---------------------------------------------------------------------------
# 消息管理
# ---------------------------------------------------------------------------

@mcp.tool()
def save_message(conversation_id: str, role: str, content: str) -> str:
    """
    保存一条消息到指定会话。

    编排流程：Step 0 先调用 save_message(conversation_id, "user", ...) 存入用户消息，
    conversation_chat 才能从 DB 读取到本轮 query。

    参数：
        conversation_id: 会话 ID。
        role:            "user" 或 "assistant"。
        content:         消息内容。

    返回：
        操作结果描述，包含新消息的 id。
    """
    if role not in ("user", "assistant"):
        raise ValueError(f"role 必须是 'user' 或 'assistant'，收到：{role!r}")
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (conversation_id, role, content, _now()),
        )
        msg_id = cur.lastrowid
    return f"消息已保存，id={msg_id}, role={role}"


@mcp.tool()
def get_history(conversation_id: str, k: int = 10) -> str:
    """
    获取最近 k 轮对话，格式化为可读字符串。

    用于 subagent 查阅近期对话（quality-check、topic-suggest、recall-* 等）。
    conversation_chat() 内部已自动读取历史，无需调用此工具传给它。

    参数：
        conversation_id: 会话 ID。
        k:               轮数，默认 10。

    返回：
        格式化字符串：
        【小雨】：在呢 怎了
        【用户】：最近想去日本旅游
    """
    with _db() as conn:
        session_row = conn.execute(
            "SELECT persona_name, user_name FROM sessions WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
        persona_name = session_row["persona_name"] if session_row else "角色"
        user_name = session_row["user_name"] if session_row else "用户"

        rows = conn.execute(
            """
            SELECT role, content FROM (
                SELECT id, role, content FROM messages
                WHERE conversation_id = ?
                ORDER BY id DESC LIMIT ?
            ) ORDER BY id ASC
            """,
            (conversation_id, k * 2),
        ).fetchall()

    lines = []
    for r in rows:
        name = persona_name if r["role"] == "assistant" else user_name
        lines.append(f"【{name}】：{r['content']}")
    return "\n".join(lines)


@mcp.tool()
def get_latest_user_message(conversation_id: str) -> str:
    """
    获取指定会话最新一条用户消息（即本轮 query）。

    编排核心在 Step 0 已通过 save_message 存入，subagent 通过此工具获取当前 query。

    参数：
        conversation_id: 会话 ID。

    返回：
        用户消息字符串；未找到时返回空字符串。
    """
    with _db() as conn:
        row = conn.execute(
            "SELECT content FROM messages WHERE conversation_id = ? AND role = 'user' ORDER BY id DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
    return row["content"] if row else ""


@mcp.tool()
def get_all_messages(conversation_id: str) -> str:
    """
    获取指定会话的全部消息（按时间正序）。

    用于历史压缩、导出等场景。

    返回：
        JSON 数组，每项包含 id、role、content、created_at。
    """
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY id ASC",
            (conversation_id,),
        ).fetchall()
    return json.dumps([dict(r) for r in rows], ensure_ascii=False)


@mcp.tool()
def count_messages(conversation_id: str) -> str:
    """
    统计指定会话的消息总条数和轮数。

    用于判断是否需要触发历史压缩（HC-1）。

    返回：
        JSON：{"total": N, "rounds": N}，rounds = total // 2
    """
    with _db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()[0]
    return json.dumps({"total": total, "rounds": total // 2}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 用户画像
# ---------------------------------------------------------------------------

@mcp.tool()
def update_profile(conversation_id: str, slot_name: str, value: str) -> str:
    """
    新增或更新用户画像的某个槽位。

    参数：
        conversation_id: 会话 ID。
        slot_name:       槽位名，如 age、city、hobby。
        value:           槽位值；模糊信息加前缀"似乎"。
    """
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO user_profile (conversation_id, slot_name, value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(conversation_id, slot_name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (conversation_id, slot_name, value, _now()),
        )
    return f"画像已更新：{slot_name} = {value}"


@mcp.tool()
def get_profile(conversation_id: str) -> str:
    """
    获取指定会话的完整用户画像。

    返回：
        JSON 对象：{"slot_name": {"value": "...", "updated_at": "..."}, ...}
    """
    with _db() as conn:
        rows = conn.execute(
            "SELECT slot_name, value, updated_at FROM user_profile WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchall()
    result = {r["slot_name"]: {"value": r["value"], "updated_at": r["updated_at"]} for r in rows}
    return json.dumps(result, ensure_ascii=False)


@mcp.tool()
def get_profile_slot(conversation_id: str, slot_name: str) -> str:
    """
    获取用户画像中某个槽位的值。

    返回：
        JSON：{"value": "...", "updated_at": "..."} 或 null。
    """
    with _db() as conn:
        row = conn.execute(
            "SELECT value, updated_at FROM user_profile WHERE conversation_id = ? AND slot_name = ?",
            (conversation_id, slot_name),
        ).fetchone()
    if row is None:
        return "null"
    return json.dumps(dict(row), ensure_ascii=False)


@mcp.tool()
def clear_profile_slot(conversation_id: str, slot_name: str) -> str:
    """
    删除用户画像中某个槽位（仅在用户明确否定时调用）。
    """
    with _db() as conn:
        conn.execute(
            "DELETE FROM user_profile WHERE conversation_id = ? AND slot_name = ?",
            (conversation_id, slot_name),
        )
    return f"画像槽位已删除：{slot_name}"


@mcp.tool()
def clear_all_profile(conversation_id: str) -> str:
    """
    清空指定会话的全部用户画像（新对话开始时重置）。
    """
    with _db() as conn:
        conn.execute(
            "DELETE FROM user_profile WHERE conversation_id = ?", (conversation_id,)
        )
    return f"会话 {conversation_id} 的用户画像已清空"


# ---------------------------------------------------------------------------
# 用户记忆
# ---------------------------------------------------------------------------

@mcp.tool()
def add_user_memory(conversation_id: str, content: str, memory_date: str = "") -> str:
    """
    添加一条用户记忆。

    参数：
        conversation_id: 会话 ID。
        content:         记忆内容，如"用户刚失恋，情绪低落"。
        memory_date:     事件日期 YYYY-MM-DD；不填默认今天。

    返回：
        操作结果描述，包含新记忆的 id。
    """
    mem_date = memory_date.strip() if memory_date.strip() else _today()
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO user_memory (conversation_id, content, memory_date, created_at) VALUES (?, ?, ?, ?)",
            (conversation_id, content, mem_date, _now()),
        )
        mem_id = cur.lastrowid
    return f"用户记忆已添加，id={mem_id}，日期={mem_date}"


@mcp.tool()
def search_user_memory(conversation_id: str, keywords: str, limit: int = 5) -> str:
    """
    关键词搜索用户记忆，按时间加权排序。

    参数：
        conversation_id: 会话 ID。
        keywords:        空格分隔的关键词，OR 逻辑匹配。
        limit:           最多返回条数，默认 5。

    返回：
        JSON 数组，每项包含 id、content、memory_date、created_at。
    """
    kws = [k for k in re.split(r"\s+", keywords.strip()) if k]
    if not kws:
        return "[]"
    conditions = " OR ".join(["LOWER(content) LIKE ?" for _ in kws])
    params = [f"%{k.lower()}%" for k in kws]
    with _db() as conn:
        rows = conn.execute(
            f"""
            SELECT id, content, memory_date, created_at FROM user_memory
            WHERE conversation_id = ? AND ({conditions})
            ORDER BY memory_date DESC, created_at DESC
            LIMIT ?
            """,
            (conversation_id, *params, limit),
        ).fetchall()
    return json.dumps([dict(r) for r in rows], ensure_ascii=False)


@mcp.tool()
def get_all_user_memory(conversation_id: str) -> str:
    """
    获取指定会话的全部用户记忆（按日期倒序）。

    返回：
        JSON 数组，每项包含 id、content、memory_date、created_at。
    """
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, content, memory_date, created_at FROM user_memory WHERE conversation_id = ? ORDER BY memory_date DESC, created_at DESC",
            (conversation_id,),
        ).fetchall()
    return json.dumps([dict(r) for r in rows], ensure_ascii=False)


@mcp.tool()
def delete_user_memory(conversation_id: str, memory_id: int) -> str:
    """
    删除指定 id 的用户记忆。
    """
    with _db() as conn:
        conn.execute(
            "DELETE FROM user_memory WHERE id = ? AND conversation_id = ?",
            (memory_id, conversation_id),
        )
    return f"用户记忆 id={memory_id} 已删除"


# ---------------------------------------------------------------------------
# 角色新设定
# ---------------------------------------------------------------------------

@mcp.tool()
def add_persona_memory(conversation_id: str, content: str, memory_date: str = "") -> str:
    """
    添加一条角色新设定。

    参数：
        conversation_id: 会话 ID。
        content:         设定内容，如"有一辆小电驴"。
        memory_date:     日期 YYYY-MM-DD；不填默认今天；无法确定可传"聊天时"。

    返回：
        操作结果描述，包含新设定的 id。
    """
    mem_date = memory_date.strip() if memory_date.strip() else _today()
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO persona_memory (conversation_id, content, memory_date, created_at) VALUES (?, ?, ?, ?)",
            (conversation_id, content, mem_date, _now()),
        )
        mem_id = cur.lastrowid
    return f"角色新设定已添加，id={mem_id}，日期={mem_date}"


@mcp.tool()
def get_persona_memory(conversation_id: str) -> str:
    """
    获取指定会话的全部角色新设定（按时间正序）。

    返回：
        JSON 数组，每项包含 id、content、memory_date、created_at。
    """
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, content, memory_date, created_at FROM persona_memory WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
    return json.dumps([dict(r) for r in rows], ensure_ascii=False)


@mcp.tool()
def search_persona_memory(conversation_id: str, keywords: str, limit: int = 5) -> str:
    """
    关键词搜索角色新设定。

    参数：
        conversation_id: 会话 ID。
        keywords:        空格分隔的关键词。
        limit:           最多返回条数，默认 5。

    返回：
        JSON 数组，每项包含 id、content、memory_date。
    """
    kws = [k for k in re.split(r"\s+", keywords.strip()) if k]
    if not kws:
        return "[]"
    conditions = " OR ".join(["LOWER(content) LIKE ?" for _ in kws])
    params = [f"%{k.lower()}%" for k in kws]
    with _db() as conn:
        rows = conn.execute(
            f"""
            SELECT id, content, memory_date FROM persona_memory
            WHERE conversation_id = ? AND ({conditions})
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (conversation_id, *params, limit),
        ).fetchall()
    return json.dumps([dict(r) for r in rows], ensure_ascii=False)


@mcp.tool()
def delete_persona_memory(conversation_id: str, memory_id: int) -> str:
    """
    删除指定 id 的角色新设定。
    """
    with _db() as conn:
        conn.execute(
            "DELETE FROM persona_memory WHERE id = ? AND conversation_id = ?",
            (memory_id, conversation_id),
        )
    return f"角色新设定 id={memory_id} 已删除"


# ---------------------------------------------------------------------------
# 对话摘要
# ---------------------------------------------------------------------------

@mcp.tool()
def save_summary(conversation_id: str, content: str, rounds_covered: str) -> str:
    """
    保存或更新对话摘要（每个 session 只保留一条，覆盖更新）。

    参数：
        conversation_id: 会话 ID。
        content:         Markdown 格式摘要，建议 ≤400 字。
        rounds_covered:  压缩覆盖的轮次范围，如 "1-20"。
    """
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO summaries (conversation_id, content, rounds_covered, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(conversation_id) DO UPDATE SET
                content=excluded.content,
                rounds_covered=excluded.rounds_covered,
                updated_at=excluded.updated_at
            """,
            (conversation_id, content, rounds_covered, _now()),
        )
    return f"摘要已保存，覆盖轮次：{rounds_covered}"


@mcp.tool()
def get_summary(conversation_id: str) -> str:
    """
    获取指定会话的对话摘要。

    返回：
        JSON：{"content": "...", "rounds_covered": "1-20", "updated_at": "..."}
        或 null（尚无摘要时）。
    """
    with _db() as conn:
        row = conn.execute(
            "SELECT content, rounds_covered, updated_at FROM summaries WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
    if row is None:
        return "null"
    return json.dumps(dict(row), ensure_ascii=False)


@mcp.tool()
def delete_summary(conversation_id: str) -> str:
    """
    删除指定会话的对话摘要。
    """
    with _db() as conn:
        conn.execute(
            "DELETE FROM summaries WHERE conversation_id = ?", (conversation_id,)
        )
    return f"会话 {conversation_id} 的摘要已删除"


# ---------------------------------------------------------------------------
# Personas（不需要 conversation_id）
# ---------------------------------------------------------------------------

@mcp.tool()
def get_persona(name: str) -> str:
    """
    获取指定角色的完整信息。

    返回：
        JSON：{name, persona_content, fewshot_content, model} 或 null。
    """
    with _db() as conn:
        row = conn.execute(
            "SELECT name, persona_content, fewshot_content, model FROM personas WHERE name = ?",
            (name,),
        ).fetchone()
    if row is None:
        return "null"
    return json.dumps(dict(row), ensure_ascii=False)


@mcp.tool()
def list_personas() -> str:
    """
    列出所有可用角色（不含全文）。

    返回：
        JSON 数组，每项包含 name、model、created_at、updated_at。
    """
    with _db() as conn:
        rows = conn.execute(
            "SELECT name, model, created_at, updated_at FROM personas ORDER BY name",
        ).fetchall()
    return json.dumps([dict(r) for r in rows], ensure_ascii=False)


@mcp.tool()
def get_fewshot(name: str) -> str:
    """
    获取指定角色的全量 fewshot 示例文本。

    由 recall-fewshot agent 调用，获取全量后由模型筛选 3-5 条。

    返回：
        fewshot_content 全文；不存在或为空时返回空字符串。
    """
    with _db() as conn:
        row = conn.execute(
            "SELECT fewshot_content FROM personas WHERE name = ?", (name,)
        ).fetchone()
    if row is None:
        return ""
    return row["fewshot_content"] or ""


@mcp.tool()
def upsert_persona(name: str, persona_content: str, fewshot_content: str = "", model: str = "openai") -> str:
    """
    新增或更新角色配置。

    参数：
        name:            角色名称。
        persona_content: persona.md 全文。
        fewshot_content: fewshot.md 全文，可为空。
        model:           模型标识，默认 "openai"。
    """
    now = _now()
    with _db() as conn:
        existing = conn.execute(
            "SELECT name FROM personas WHERE name = ?", (name,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE personas SET persona_content=?, fewshot_content=?, model=?, updated_at=? WHERE name=?",
                (persona_content, fewshot_content, model, now, name),
            )
            action = "已更新"
        else:
            conn.execute(
                "INSERT INTO personas (name, persona_content, fewshot_content, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (name, persona_content, fewshot_content, model, now, now),
            )
            action = "已创建"
    return f"角色 {name} {action}，model={model}"


@mcp.tool()
def delete_persona(name: str) -> str:
    """
    删除指定角色配置。
    """
    with _db() as conn:
        conn.execute("DELETE FROM personas WHERE name = ?", (name,))
    return f"角色 {name} 已删除"


if __name__ == "__main__":
    mcp.run()
