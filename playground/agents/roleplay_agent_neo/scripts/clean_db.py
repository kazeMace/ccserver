#!/usr/bin/env python3
"""
清理 chat.db 数据库

支持多种清理模式：
  --session <id>   删除指定 conversation_id 的全部数据（级联删除所有关联表）
  --before <date>  删除指定日期之前创建的所有会话（格式：YYYY-MM-DD）
  --all            清空所有数据（保留表结构）
  --vacuum         执行 VACUUM 压缩数据库文件（可与其他选项组合）

用法示例：
    python scripts/clean_db.py --session 小雨_20260407_151400
    python scripts/clean_db.py --before 2026-03-01
    python scripts/clean_db.py --all
    python scripts/clean_db.py --before 2026-03-01 --vacuum
    python scripts/clean_db.py --vacuum
"""

import argparse
import sqlite3
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB = _PROJECT_ROOT / "chat.db"

_ALL_TABLES = ["summaries", "persona_memory", "user_memory", "user_profile", "messages", "sessions"]


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        print(f"[ERROR] 数据库文件不存在：{db_path}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _confirm(prompt: str) -> bool:
    try:
        ans = input(f"{prompt} [y/N] ").strip().lower()
        return ans == "y"
    except (KeyboardInterrupt, EOFError):
        print()
        return False


def _print_stats(conn: sqlite3.Connection) -> None:
    """打印各表当前行数。"""
    for table in reversed(_ALL_TABLES):
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table:<20} {count} 条")
        except Exception:
            pass


def delete_session(db_path: Path, conversation_id: str, force: bool) -> None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT persona_name, user_name, created_at FROM sessions WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            print(f"[WARN] 未找到会话：{conversation_id}")
            return

        persona, user, created = row
        msg_count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()[0]

        print(f"[INFO] 即将删除会话：{conversation_id}")
        print(f"       角色={persona}  用户={user}  创建于={created}  消息={msg_count} 条")

        if not force and not _confirm("确认删除？"):
            print("[取消]")
            return

        conn.execute("DELETE FROM sessions WHERE conversation_id = ?", (conversation_id,))
        conn.commit()
        print(f"[OK] 会话 {conversation_id} 及关联数据已删除（级联）")
    finally:
        conn.close()


def delete_before(db_path: Path, before_date: str, force: bool) -> None:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT conversation_id, persona_name, created_at FROM sessions WHERE created_at < ? ORDER BY created_at",
            (before_date,),
        ).fetchall()

        if not rows:
            print(f"[INFO] 没有 {before_date} 之前的会话")
            return

        print(f"[INFO] 找到 {len(rows)} 个会话将被删除：")
        for r in rows:
            print(f"  {r[0]}  persona={r[1]}  created={r[2][:10]}")

        if not force and not _confirm("确认删除以上所有会话？"):
            print("[取消]")
            return

        ids = [r[0] for r in rows]
        conn.execute(
            f"DELETE FROM sessions WHERE conversation_id IN ({','.join('?' * len(ids))})",
            ids,
        )
        conn.commit()
        print(f"[OK] {len(ids)} 个会话及关联数据已删除（级联）")
    finally:
        conn.close()


def delete_all(db_path: Path, force: bool) -> None:
    conn = _connect(db_path)
    try:
        print("[INFO] 当前数据库状态：")
        _print_stats(conn)

        if not force and not _confirm("\n确认清空所有数据？（表结构保留）"):
            print("[取消]")
            return

        # 按外键依赖顺序删除
        for table in _ALL_TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        print("[OK] 所有数据已清空，表结构保留")
    finally:
        conn.close()


def vacuum_db(db_path: Path) -> None:
    size_before = db_path.stat().st_size
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("VACUUM")
        conn.commit()
    finally:
        conn.close()
    size_after = db_path.stat().st_size
    saved = (size_before - size_after) / 1024
    print(f"[OK] VACUUM 完成，释放 {saved:.1f} KB（{size_before // 1024} KB → {size_after // 1024} KB）")


def show_stats(db_path: Path) -> None:
    conn = _connect(db_path)
    try:
        print(f"[INFO] 数据库：{db_path}（{db_path.stat().st_size // 1024} KB）")
        print("[INFO] 各表行数：")
        _print_stats(conn)

        sessions = conn.execute(
            "SELECT conversation_id, persona_name, user_name, created_at FROM sessions ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        if sessions:
            print("\n[INFO] 最近 10 个会话：")
            for s in sessions:
                msg_count = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (s[0],)
                ).fetchone()[0]
                print(f"  {s[0]}  persona={s[1]}  user={s[2]}  消息={msg_count}条  created={s[3][:10]}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="清理 chat.db 数据库")
    parser.add_argument("--db", type=Path, default=_DEFAULT_DB, help="数据库文件路径")
    parser.add_argument("--session", metavar="CONVERSATION_ID", help="删除指定会话的全部数据")
    parser.add_argument("--before", metavar="YYYY-MM-DD", help="删除该日期之前创建的所有会话")
    parser.add_argument("--all", action="store_true", help="清空所有数据（保留表结构）")
    parser.add_argument("--vacuum", action="store_true", help="压缩数据库文件")
    parser.add_argument("-f", "--force", action="store_true", help="跳过确认提示，直接执行")
    parser.add_argument("--stats", action="store_true", help="只显示数据库统计信息")

    args = parser.parse_args()

    # 无参数时显示统计
    if not any([args.session, args.before, args.all, args.vacuum, args.stats]):
        show_stats(args.db)
        return

    if args.stats:
        show_stats(args.db)
        return

    if args.session:
        delete_session(args.db, args.session, args.force)

    if args.before:
        delete_before(args.db, args.before, args.force)

    if args.all:
        delete_all(args.db, args.force)

    if args.vacuum:
        vacuum_db(args.db)


if __name__ == "__main__":
    main()
