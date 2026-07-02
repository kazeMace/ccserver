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
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.completion import WordCompleter

from dotenv import load_dotenv
load_dotenv()

from ccserver import (
    AgentRunner,
    SessionManager,
)
from ccserver.configuration import get_process_config
from ccserver.storage import build_storage
from ccserver.emitters.tui import TUIEmitter, Spinner, RESET, BOLD, DIM, CYAN, GREEN, YELLOW, RED, gradient_text, rainbow_text
from ccserver.emitters import FilterEmitter
from ccserver.log import setup_logging

setup_logging()

# 进程级配置（解析一次，TUI 与所有 Session 共享）
_CFG = get_process_config()


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

# 渐变色配色方案
_LOGO_START = (59, 130, 246)    # #3B82F6 亮蓝
_LOGO_END   = (139, 92, 246)     # #8B5CF6 紫


def print_logo():
    """打印蓝紫渐变的 ASCII logo。"""
    print()
    for line in LOGO.strip("\n").splitlines():
        print(f"  {gradient_text(line, _LOGO_START, _LOGO_END)}")
    # 底部 slogan 彩虹色
    slogan = "  powered by multi-provider LLM"
    print(f"  {rainbow_text(slogan)}{RESET}\n")


def print_help():
    print(f"""
{BOLD}Commands:{RESET}
  {CYAN}/clear{RESET}                  Start a new session
  {CYAN}/session <id>{RESET}           Switch to an existing session
  {CYAN}/sessions{RESET}               List all sessions
  {CYAN}/workdir{RESET}                Show current session's workdir
  {CYAN}/verbosity [level]{RESET}      查看或切换展示详细程度
                           levels: verbose（默认）| final_only
  {CYAN}/stream{RESET}                 切换 token 流开关（on/off）
  {CYAN}/interactive{RESET}            切换交互模式开关（on/off）
  {CYAN}/q{RESET} or {CYAN}exit{RESET}              Quit
""")


def _read_system_file(path: str | None) -> str | None:
    """读取 system prompt 文件，返回文本内容。"""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"system file 不存在: {path}")
    return p.read_text(encoding="utf-8")


# ─── 定时任务 Push 监听 ───────────────────────────────────────────────────────


async def _push_listener(session, emitter) -> None:
    """
    进程内 EventBus 监听协程，接收定时任务 / 后台 Agent 的主动推送。

    直接订阅 session.event_bus，只关注 DONE 事件，收到后通过 TUIEmitter 输出。
    在主循环等待用户输入（prompt_async）期间后台运行。

    Args:
        session:  当前 Session 实例，提供 event_bus。
        emitter:  TUIEmitter 实例，用于统一输出格式。
    """
    from ccserver.event_bus import EventType

    event_bus = getattr(session, "event_bus", None)
    if event_bus is None:
        return

    sub_id = f"tui_push_{session.id[:8]}"

    try:
        async with event_bus.subscribe(
            sub_id,
            filter_fn=lambda e: e.type == EventType.DONE,
        ) as sub:
            while True:
                try:
                    event = await sub.get(timeout=5.0)
                except asyncio.CancelledError:
                    break

                if event is None:
                    continue

                content = (event.payload or {}).get("content", "")
                if not content:
                    continue

                # 在主循环等待用户输入时，输出到终端
                # prompt_toolkit 的 prompt_async 不会被打断，print 会显示在 prompt 上方
                print(
                    f"\n{CYAN}┌─ 📬 服务端推送 ──────────────────────────────{RESET}",
                    flush=True,
                )
                print(f"{CYAN}│{RESET} {content}", flush=True)
                print(
                    f"{CYAN}└───────────────────────────────────────────────{RESET}\n",
                    flush=True,
                )

    except asyncio.CancelledError:
        pass
    except Exception as e:
        # 订阅出错静默忽略，不影响主循环
        print(f"{YELLOW}[push] 监听出错: {e}{RESET}", flush=True)


# ─── Main TUI loop ────────────────────────────────────────────────────────────


async def tui_main(system: str | None = None, append_system: bool = False):
    print_logo()

    _storage = build_storage(
        _CFG.infra.storage_backend, _CFG.infra.sessions_base, _CFG.infra.db_path,
        mongo_uri=_CFG.infra.mongo_uri, mongo_db=_CFG.infra.mongo_db,
        redis_url=_CFG.infra.redis_url, redis_cache_size=_CFG.infra.redis_cache_size, redis_ttl=_CFG.infra.redis_ttl,
    )
    session_manager = SessionManager(storage=_storage, process_config=_CFG)
    runner = AgentRunner(system=system, append_system=append_system)
    session = session_manager.create()
    emitter = TUIEmitter()

    # 启动 EventBus push 监听协程：在主循环等待用户输入期间接收定时任务推送
    _push_task: asyncio.Task = asyncio.create_task(
        _push_listener(session, emitter)
    )

    # 三个独立的输出控制参数（可通过 slash 命令修改）
    current_verbosity: str = "verbose"   # "verbose" | "final_only"
    current_stream: bool = True          # 是否推 token 流
    current_interactive: bool = True     # 是否等待用户交互

    # 当前正在运行的 agent task，供 ESC 中断使用
    _running_task: asyncio.Task | None = None

    def _status_line() -> str:
        s = "on" if current_stream else "off"
        i = "on" if current_interactive else "off"
        return f"verbosity: {current_verbosity} | stream: {s} | interactive: {i}"

    # slash 命令列表，用于 / 前缀补全
    _SLASH_COMMANDS = [
        "/clear", "/session", "/sessions", "/workdir",
        "/verbosity", "/stream", "/interactive",
        "/status", "/help", "/q",
    ]

    # ESC 键绑定：取消当前正在运行的 agent task
    _kb = KeyBindings()

    @_kb.add("escape")
    def _on_escape(event):
        """ESC：如果有正在运行的 agent task，取消它；否则清空当前输入行。"""
        if _running_task is not None and not _running_task.done():
            _running_task.cancel()
        else:
            event.current_buffer.reset()

    # / 开头时激活补全
    _completer = WordCompleter(_SLASH_COMMANDS, pattern=r"\/\S*", sentence=True)

    print(
        f"{gradient_text('CCServer', _LOGO_START, _LOGO_END)} TUI{RESET}"
        f" | {DIM}{_CFG.model.model_id} | "
        f"session: {session.id[:8]}{RESET}\n"
        f"{DIM}workdir: {session.workdir}{RESET}\n"
        f"{DIM}project: {_CFG.infra.project_dir or '(none)'}{RESET}\n"
        f"{DIM}{_status_line()}{RESET}\n"
        f"Type {CYAN}/help{RESET} for commands.\n"
    )

    _pt_session = PromptSession(
        [("class:prompt", "✏️  ")],
        style=PTStyle.from_dict({"prompt": "bold ansiblue"}),
        key_bindings=_kb,
        completer=_completer,
        complete_while_typing=True,
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

            if user_input.startswith("/verbosity"):
                parts = user_input.split(None, 1)
                if len(parts) == 1:
                    print(f"{DIM}verbosity: {RESET}{CYAN}{current_verbosity}{RESET}  {DIM}可选: verbose | final_only{RESET}")
                else:
                    v = parts[1].strip()
                    if v in ("verbose", "final_only"):
                        current_verbosity = v
                        if v == "final_only":
                            current_interactive = False
                            print(f"{GREEN}⏺ verbosity → {v}  （已强制关闭 interactive）{RESET}")
                        else:
                            print(f"{GREEN}⏺ verbosity → {v}{RESET}")
                    else:
                        print(f"{RED}⏺ 无效值: {v}，可选: verbose | final_only{RESET}")
                continue

            if user_input == "/stream":
                current_stream = not current_stream
                label = "on" if current_stream else "off"
                print(f"{GREEN}⏺ stream → {label}{RESET}")
                continue

            if user_input == "/interactive":
                if current_verbosity == "final_only":
                    print(f"{YELLOW}⚠ verbosity=final_only 时 interactive 强制为 off{RESET}")
                else:
                    current_interactive = not current_interactive
                    label = "on" if current_interactive else "off"
                    print(f"{GREEN}⏺ interactive → {label}{RESET}")
                continue

            if user_input == "/status":
                print(
                    f"{BOLD}Session:{RESET}     {DIM}{session.id}{RESET}\n"
                    f"{BOLD}Workdir:{RESET}     {DIM}{session.workdir}{RESET}\n"
                    f"{BOLD}Model:{RESET}       {DIM}{_CFG.model.model_id}{RESET}\n"
                    f"{BOLD}Verbosity:{RESET}   {CYAN}{current_verbosity}{RESET}\n"
                    f"{BOLD}Stream:{RESET}      {CYAN}{'on' if current_stream else 'off'}{RESET}\n"
                    f"{BOLD}Interactive:{RESET} {CYAN}{'on' if current_interactive else 'off'}{RESET}"
                )
                continue

            # ── Agent call ─────────────────────────────────────────────────
            active_emitter = FilterEmitter(
                emitter,
                verbosity=current_verbosity,
                stream=current_stream,
                interactive=current_interactive,
            )

            spinner = Spinner("Thinking")
            emitter.set_spinner(spinner)
            spinner.start()
            try:
                # 用 asyncio.Task 包装，让 ESC 键绑定可以 cancel 它
                _running_task = asyncio.create_task(
                    runner.run(session, user_input, active_emitter)
                )
                await _running_task
            except asyncio.CancelledError:
                print(f"\n{YELLOW}⏹ 已中断{RESET}")
            finally:
                _running_task = None
                emitter._stop_spinner()

        except (KeyboardInterrupt, EOFError):
            print()
            break
        except Exception as err:
            print(f"{RED}⏺ Error: {err}{RESET}")

    # 退出时取消 push 监听协程
    _push_task.cancel()
    try:
        await _push_task
    except asyncio.CancelledError:
        pass

    if hasattr(_storage, "close"):
        await _storage.close()


def main():
    parser = argparse.ArgumentParser(description="CCServer TUI")
    parser.add_argument("--system-file", metavar="PATH", help="注入的 system prompt md 文件路径（覆盖 CCSERVER_INJECT_SYSTEM_FILE）")
    parser.add_argument("--append-system", action="store_true", default=None, help="追加到 workflow 末尾（覆盖 CCSERVER_APPEND_SYSTEM）")
    args = parser.parse_args()

    # 命令行参数优先，否则读环境变量（与 server.py 对齐）
    system_path = args.system_file or _CFG.agent.inject_system_file
    append = args.append_system if args.append_system is not None else _CFG.agent.append_system

    try:
        system = _read_system_file(system_path)
    except FileNotFoundError as e:
        print(f"{RED}⏺ {e}{RESET}")
        return

    asyncio.run(tui_main(system=system, append_system=append))


if __name__ == "__main__":
    main()
