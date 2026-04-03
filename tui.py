#!/usr/bin/env python3
"""
CCServer TUI — terminal interface.
"""

import argparse
import asyncio
import os
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style as PTStyle

from dotenv import load_dotenv
load_dotenv()

from ccserver import (
    AgentRunner,
    MODEL,
    Session,
    SessionManager,
    SESSIONS_BASE,
)
from ccserver.config import (
    PROJECT_DIR, DB_PATH, SYSTEM_FILE, APPEND_SYSTEM,
    STORAGE_BACKEND, MONGO_URI, MONGO_DB, REDIS_URL, REDIS_CACHE_SIZE, REDIS_TTL,
)
from ccserver.storage import build_storage
from ccserver.core.emitter.tui_emitter import TUIEmitter, Spinner, RESET, BOLD, DIM, BLUE, CYAN, GREEN, YELLOW, RED
from ccserver.log import setup_logging

setup_logging()


# ─── Helpers ──────────────────────────────────────────────────────────────────


def separator() -> str:
    try:
        cols = os.get_terminal_size().columns
    except OSError:
        cols = 80
    return f"{DIM}{'─' * min(cols, 80)}{RESET}"


LOGO = r"""
  ██████╗ ██╗   ██╗ ██████╗ ██████╗
  ██╔══██╗╚██╗ ██╔╝██╔════╝██╔════╝
  ██████╔╝ ╚████╔╝ ██║     ██║
  ██╔═══╝   ╚██╔╝  ██║     ██║
  ██║        ██║   ╚██████╗╚██████╗
  ╚═╝        ╚═╝    ╚═════╝ ╚═════╝"""


def print_help():
    print(f"""
{BOLD}Commands:{RESET}
  {CYAN}/clear{RESET}           Start a new session
  {CYAN}/session <id>{RESET}    Switch to an existing session
  {CYAN}/sessions{RESET}        List all sessions
  {CYAN}/workdir{RESET}         Show current session's workdir
  {CYAN}/q{RESET} or {CYAN}exit{RESET}       Quit
""")


def _read_system_file(path: str | None) -> str | None:
    """读取 system prompt 文件，返回文本内容。"""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"system file 不存在: {path}")
    return p.read_text(encoding="utf-8")


# ─── Main TUI loop ────────────────────────────────────────────────────────────


async def tui_main(system: str | None = None, append_system: bool = False):
    print(f"\n{CYAN}", end="")
    for line in LOGO.strip("\n").splitlines():
        print(f"  {line}")
    print(RESET)

    _storage = build_storage(
        STORAGE_BACKEND, SESSIONS_BASE, DB_PATH,
        mongo_uri=MONGO_URI, mongo_db=MONGO_DB,
        redis_url=REDIS_URL, redis_cache_size=REDIS_CACHE_SIZE, redis_ttl=REDIS_TTL,
    )
    session_manager = SessionManager(SESSIONS_BASE, storage=_storage)
    runner = AgentRunner(system=system, append_system=append_system)
    session = session_manager.create()
    emitter = TUIEmitter()

    print(
        f"{BOLD}CCServer TUI{RESET} | {DIM}{MODEL} | "
        f"session: {session.id[:8]}{RESET}\n"
        f"{DIM}workdir: {session.workdir}{RESET}\n"
        f"{DIM}project: {PROJECT_DIR}{RESET}\n"
        f"Type {CYAN}/help{RESET} for commands.\n"
    )

    _pt_session = PromptSession(
        [("class:prompt", "✏️  ")],
        style=PTStyle.from_dict({"prompt": "bold ansiblue"}),
    )

    while True:
        try:
            print(separator())
            user_input = (await _pt_session.prompt_async()).strip()

            if not user_input:
                continue

            print(separator())

            # ── Built-in commands ──────────────────────────────────────────
            if user_input in ("/q", "exit"):
                break

            if user_input == "/help":
                print_help()
                continue

            if user_input == "/clear":
                session = session_manager.create()
                print(
                    f"{GREEN}⏺ New session: {session.id[:8]}{RESET}\n"
                    f"{DIM}workdir: {session.workdir}{RESET}"
                )
                continue

            if user_input == "/workdir":
                print(f"{DIM}{session.workdir}{RESET}")
                continue

            if user_input == "/sessions":
                sessions = session_manager.list_all()
                if not sessions:
                    print(f"{DIM}No sessions found.{RESET}")
                else:
                    for s in sessions:
                        marker = "▶" if s["id"] == session.id else " "
                        print(f"{DIM}{marker} {s['id'][:8]}  {s['updated_at'][:19]}  {s['workdir']}{RESET}")
                continue

            if user_input.startswith("/session "):
                sid = user_input.split(" ", 1)[1].strip()
                loaded = session_manager.get(sid)
                if loaded:
                    session = loaded
                    print(
                        f"{GREEN}⏺ Switched to: {sid[:8]}{RESET} "
                        f"({DIM}{len(session.messages)} messages{RESET})"
                    )
                else:
                    print(f"{RED}⏺ Session not found: {sid}{RESET}")
                continue

            # ── Agent call ─────────────────────────────────────────────────
            spinner = Spinner("Thinking")
            emitter.set_spinner(spinner)
            spinner.start()
            try:
                await runner.run(session, user_input, emitter)
            finally:
                emitter._stop_spinner()

        except (KeyboardInterrupt, EOFError):
            print()
            break
        except Exception as err:
            print(f"{RED}⏺ Error: {err}{RESET}")

    if hasattr(_storage, "close"):
        await _storage.close()


def main():
    parser = argparse.ArgumentParser(description="CCServer TUI")
    parser.add_argument("--system-file", metavar="PATH", help="注入的 system prompt md 文件路径（覆盖 CCSERVER_SYSTEM_FILE）")
    parser.add_argument("--append-system", action="store_true", default=None, help="追加到 workflow 末尾（覆盖 CCSERVER_APPEND_SYSTEM）")
    args = parser.parse_args()

    # 命令行参数优先，否则读环境变量（与 server.py 对齐）
    system_path = args.system_file or SYSTEM_FILE
    append = args.append_system if args.append_system is not None else APPEND_SYSTEM

    try:
        system = _read_system_file(system_path)
    except FileNotFoundError as e:
        print(f"{RED}⏺ {e}{RESET}")
        return

    asyncio.run(tui_main(system=system, append_system=append))


if __name__ == "__main__":
    main()
