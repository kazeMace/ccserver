#!/usr/bin/env python3
"""
初始化 chat.db 数据库

创建所有表结构、索引，并写入 personas 种子数据。幂等操作，重复执行不会破坏已有数据。

用法：
    python scripts/init_db.py
    python scripts/init_db.py --db /path/to/custom.db  # 指定数据库路径
    python scripts/init_db.py --no-seed                 # 只建表，不写入 persona 种子数据
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB = _PROJECT_ROOT / "chat.db"
_PERSONAS_DIR = _PROJECT_ROOT / "personas"

DDL = """
-- 会话表
CREATE TABLE IF NOT EXISTS sessions (
    conversation_id  TEXT PRIMARY KEY,
    persona_name     TEXT NOT NULL,
    user_name        TEXT NOT NULL DEFAULT '用户',
    created_at       TEXT NOT NULL
);

-- 消息表（替代 chat.jsonl）
CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  TEXT NOT NULL REFERENCES sessions(conversation_id) ON DELETE CASCADE,
    role             TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content          TEXT NOT NULL,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);

-- 用户画像表（替代 user_profile.json）
CREATE TABLE IF NOT EXISTS user_profile (
    conversation_id  TEXT NOT NULL REFERENCES sessions(conversation_id) ON DELETE CASCADE,
    slot_name        TEXT NOT NULL,
    value            TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (conversation_id, slot_name)
);

-- 用户记忆表（替代 user_memory.md）
CREATE TABLE IF NOT EXISTS user_memory (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  TEXT NOT NULL REFERENCES sessions(conversation_id) ON DELETE CASCADE,
    content          TEXT NOT NULL,
    memory_date      TEXT NOT NULL,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_user_memory_conv ON user_memory(conversation_id, memory_date);

-- 角色新设定表（替代 persona_memory.md）
CREATE TABLE IF NOT EXISTS persona_memory (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id  TEXT NOT NULL REFERENCES sessions(conversation_id) ON DELETE CASCADE,
    content          TEXT NOT NULL,
    memory_date      TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_persona_memory_conv ON persona_memory(conversation_id);

-- 对话摘要表（替代 summary.md，每个 session 只保留一条）
CREATE TABLE IF NOT EXISTS summaries (
    conversation_id  TEXT PRIMARY KEY REFERENCES sessions(conversation_id) ON DELETE CASCADE,
    content          TEXT NOT NULL,
    rounds_covered   TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

-- 角色表（persona 配置 + fewshot 全文）
CREATE TABLE IF NOT EXISTS personas (
    name             TEXT PRIMARY KEY,
    persona_content  TEXT NOT NULL,   -- persona.md 全文
    fewshot_content  TEXT NOT NULL DEFAULT '',  -- fewshot.md 全文
    model            TEXT NOT NULL DEFAULT 'openai',  -- 使用的模型标识
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
"""

# ---------------------------------------------------------------------------
# Persona 种子数据
# ---------------------------------------------------------------------------

# model 字段说明：
#   此处填写 .mcp.json → mcpServers.chat-model.env 中对应的 MODEL_NAME 值，
#   或预留标识供后续多模型路由使用。
PERSONA_SEEDS = [
    {"name": "小雨", "model": "openai"},
    {"name": "小北", "model": "openai"},
]


def _read_file(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def _seed_personas(conn: sqlite3.Connection) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    seeded = 0
    for seed in PERSONA_SEEDS:
        name = seed["name"]
        persona_dir = _PERSONAS_DIR / name

        persona_content = _read_file(persona_dir / "persona.md")
        fewshot_content = _read_file(persona_dir / "fewshot.md")

        if not persona_content:
            print(f"  [SKIP] personas/{name}/persona.md 不存在，跳过")
            continue

        existing = conn.execute(
            "SELECT name FROM personas WHERE name = ?", (name,)
        ).fetchone()

        if existing:
            # 更新内容但不改 created_at
            conn.execute(
                """
                UPDATE personas SET persona_content=?, fewshot_content=?, model=?, updated_at=?
                WHERE name=?
                """,
                (persona_content, fewshot_content, seed["model"], now, name),
            )
            print(f"  [UPDATE] {name}（model={seed['model']}）")
        else:
            conn.execute(
                """
                INSERT INTO personas (name, persona_content, fewshot_content, model, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (name, persona_content, fewshot_content, seed["model"], now, now),
            )
            print(f"  [INSERT] {name}（model={seed['model']}）")
        seeded += 1

    return seeded


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def init_db(db_path: Path, seed: bool = True) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    existed = db_path.exists()

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(DDL)
        conn.commit()
    finally:
        conn.close()

    if existed:
        print(f"[OK] 数据库已存在，表结构检查/补全完成：{db_path}")
    else:
        print(f"[OK] 数据库已创建：{db_path}")

    # 写入 persona 种子数据
    if seed:
        print("[INFO] 写入 persona 种子数据...")
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            n = _seed_personas(conn)
            conn.commit()
            print(f"[OK] persona 种子数据写入完成，共 {n} 条")
        finally:
            conn.close()

    # 打印表统计
    conn = sqlite3.connect(str(db_path))
    try:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        print(f"[INFO] 共 {len(tables)} 张表：{', '.join(t[0] for t in tables)}")

        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        print(f"[INFO] 共 {len(indexes)} 个索引：{', '.join(i[0] for i in indexes)}")

        persona_rows = conn.execute("SELECT name, model FROM personas ORDER BY name").fetchall()
        if persona_rows:
            print(f"[INFO] personas 表：{', '.join(f'{r[0]}({r[1]})' for r in persona_rows)}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化 chat.db 数据库")
    parser.add_argument("--db", type=Path, default=_DEFAULT_DB,
                        help=f"数据库文件路径（默认：{_DEFAULT_DB}）")
    parser.add_argument("--no-seed", action="store_true",
                        help="只建表，不写入 persona 种子数据")
    args = parser.parse_args()

    try:
        init_db(args.db, seed=not args.no_seed)
    except Exception as e:
        print(f"[ERROR] 初始化失败：{e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
