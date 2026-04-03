#!/usr/bin/env python3
"""
CCServer HTTP TUI — 通过调用 HTTP API 测试服务端接口。
需要先启动 server.py 服务。
"""

import itertools
import os
import sys
import threading
import time

import httpx
from dotenv import load_dotenv
from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.styles import Style as PTStyle

load_dotenv()

# ─── 配置 ─────────────────────────────────────────────────────────────────────

BASE_URL = os.getenv("CCSERVER_API_URL", "http://localhost:8000")

# ─── 颜色 ─────────────────────────────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
BLUE   = "\033[34m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"

LOGO = r"""
  ██████╗ ██╗   ██╗ ██████╗ ██████╗
  ██╔══██╗╚██╗ ██╔╝██╔════╝██╔════╝
  ██████╔╝ ╚████╔╝ ██║     ██║
  ██╔═══╝   ╚██╔╝  ██║     ██║
  ██║        ██║   ╚██████╗╚██████╗
  ╚═╝        ╚═╝    ╚═════╝ ╚═════╝"""


def thinking_spinner(stop_event: threading.Event):
    """在后台线程中显示 thinking 动画，直到 stop_event 被设置。"""
    frames = ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"]
    for frame in itertools.cycle(frames):
        if stop_event.is_set():
            break
        sys.stdout.write(f"\r{CYAN}{frame} thinking...{RESET}")
        sys.stdout.flush()
        time.sleep(0.08)
    sys.stdout.write("\r" + " " * 20 + "\r")
    sys.stdout.flush()


def separator() -> str:
    try:
        cols = os.get_terminal_size().columns
    except OSError:
        cols = 80
    return f"{DIM}{'─' * min(cols, 80)}{RESET}"


def print_help():
    print(f"""
{BOLD}Commands:{RESET}
  {CYAN}/clear{RESET}           创建新 session
  {CYAN}/session <id>{RESET}    切换到已有 session
  {CYAN}/sessions{RESET}        列出所有 session
  {CYAN}/q{RESET} or {CYAN}exit{RESET}       退出

{BOLD}当前后端:{RESET} {DIM}{BASE_URL}{RESET}
""")


# ─── API 调用 ─────────────────────────────────────────────────────────────────


def api_create_session(client: httpx.Client) -> dict:
    resp = client.post(f"{BASE_URL}/sessions", json={})
    resp.raise_for_status()
    return resp.json()


def api_list_sessions(client: httpx.Client) -> list:
    resp = client.get(f"{BASE_URL}/sessions")
    resp.raise_for_status()
    return resp.json()


def api_get_session(client: httpx.Client, session_id: str) -> dict | None:
    resp = client.get(f"{BASE_URL}/sessions/{session_id}")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def api_chat(client: httpx.Client, session_id: str, message: str) -> dict:
    """通过普通 HTTP 接口发送消息，阻塞等待完整响应。"""
    headers = {"X-Session-Id": session_id} if session_id else {}
    resp = client.post(f"{BASE_URL}/chat", json={"message": message}, headers=headers)
    resp.raise_for_status()
    return resp.json()


# ─── 主循环 ───────────────────────────────────────────────────────────────────


def tui_main():
    print(f"\n{CYAN}", end="")
    for line in LOGO.strip("\n").splitlines():
        print(f"  {line}")
    print(f"{RESET}{DIM}  HTTP TUI — backend: {BASE_URL}{RESET}\n")

    http = httpx.Client(timeout=900)

    # 启动时创建一个 session
    try:
        session = api_create_session(http)
    except Exception as e:
        print(f"{RED}⏺ 无法连接到后端 {BASE_URL}: {e}{RESET}")
        print(f"{DIM}请先启动 server.py: python server.py{RESET}")
        return

    session_id = session["id"]
    print(
        f"{BOLD}CCServer HTTP TUI{RESET} | {DIM}session: {session_id[:8]}{RESET}\n"
        f"Type {CYAN}/help{RESET} for commands.\n"
    )

    while True:
        try:
            print(separator())
            user_input = pt_prompt(
                [("class:prompt", "✏️  ")],
                style=PTStyle.from_dict({"prompt": "bold ansiblue"}),
            ).strip()

            if not user_input:
                continue

            print(separator())

            if user_input in ("/q", "exit"):
                break

            if user_input == "/help":
                print_help()
                continue

            if user_input == "/clear":
                session = api_create_session(http)
                session_id = session["id"]
                print(f"{GREEN}⏺ New session: {session_id[:8]}{RESET}")
                continue

            if user_input == "/sessions":
                sessions = api_list_sessions(http)
                if not sessions:
                    print(f"{DIM}No sessions found.{RESET}")
                else:
                    for s in sessions:
                        marker = "▶" if s["id"] == session_id else " "
                        print(f"{DIM}{marker} {s['id'][:8]}  {s['updated_at'][:19]}  {s['workdir']}{RESET}")
                continue

            if user_input.startswith("/session "):
                sid = user_input.split(" ", 1)[1].strip()
                s = api_get_session(http, sid)
                if s:
                    session_id = sid
                    print(f"{GREEN}⏺ Switched to: {sid[:8]}{RESET} ({DIM}{s['message_count']} messages{RESET})")
                else:
                    print(f"{RED}⏺ Session not found: {sid}{RESET}")
                continue

            # ── 普通 HTTP 对话 ─────────────────────────────────────────────
            try:
                stop_event = threading.Event()
                spinner = threading.Thread(target=thinking_spinner, args=(stop_event,), daemon=True)
                spinner.start()
                try:
                    result = api_chat(http, session_id, user_input)
                finally:
                    stop_event.set()
                    spinner.join()
                print(result.get("reply", ""))
                if result.get("session_id"):
                    session_id = result["session_id"]

            except httpx.HTTPStatusError as e:
                print(f"{RED}⏺ HTTP {e.response.status_code}: {e.response.text}{RESET}")

        except (KeyboardInterrupt, EOFError):
            print()
            break
        except Exception as err:
            print(f"{RED}⏺ Error: {err}{RESET}")

    http.close()


def main():
    tui_main()


if __name__ == "__main__":
    main()
