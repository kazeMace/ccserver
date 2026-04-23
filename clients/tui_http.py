#!/usr/bin/env python3
"""
CCServer HTTP TUI — 通过 SSE 流式接口实现实时交互。
需要先启动 server.py 服务。

架构：
    1. POST /chat/stream（SSE）→ 解析事件流
    2. 普通 token/tool_start/done → 渲染到主区域
    3. task_started / task_progress / task_done → 渲染到任务区（底部）
    4. GET /sessions/{id}/tasks → pollTasks HTTP 回退（重连恢复）
"""

import asyncio
import itertools
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field

import httpx
from dotenv import load_dotenv
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style as PTStyle
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.completion import WordCompleter

load_dotenv()

# ─── 配置 ─────────────────────────────────────────────────────────────────────

BASE_URL = os.getenv("CCSERVER_API_URL", "http://localhost:8000")

VALID_VERBOSITY = {"verbose", "final_only"}

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


# ─── 后台任务状态 ─────────────────────────────────────────────────────────────

BG_RUNNING = "running"
BG_COMPLETED = "completed"
BG_FAILED = "failed"
BG_CANCELLED = "cancelled"


@dataclass
class BGRunningTask:
    """单个后台任务的渲染状态。"""
    task_id: str
    task_type: str          # "local_bash" | "local_agent"
    description: str
    status: str = BG_RUNNING
    output: str = ""        # 累积的完整输出
    output_lines: int = 0   # 已渲染的行数（用于追加）


class BackgroundTaskManager:
    """
    管理所有后台任务的状态和渲染。

    使用方式：
        mgr = BackgroundTaskManager()
        mgr.on_task_started(event)    # SSE 收到 task_started
        mgr.on_task_progress(event)   # SSE 收到 task_progress
        mgr.on_task_done(event)       # SSE 收到 task_done
        mgr.render()                  # 渲染所有任务行
    """

    def __init__(self):
        self._tasks: dict[str, BGRunningTask] = {}

    def on_task_started(self, event: dict) -> None:
        """注册新任务。"""
        task_id = event["task_id"]
        if task_id in self._tasks:
            return
        self._tasks[task_id] = BGRunningTask(
            task_id=task_id,
            task_type=event.get("task_type", ""),
            description=event.get("description", ""),
            status=BG_RUNNING,
        )

    def on_task_progress(self, event: dict) -> None:
        """追加增量输出。"""
        task_id = event["task_id"]
        task = self._tasks.get(task_id)
        if task is None:
            return
        delta = event.get("output", "")
        if delta:
            task.output += delta

    def on_task_done(self, event: dict) -> None:
        """标记任务完成，从管理器移除。"""
        task_id = event["task_id"]
        if task_id in self._tasks:
            del self._tasks[task_id]

    def has_running(self) -> bool:
        return bool(self._tasks)

    def render(self) -> str:
        """渲染所有任务行，返回要打印的字符串。"""
        if not self._tasks:
            return ""
        lines = []
        for task in self._tasks.values():
            desc = task.description[:40]
            status_color = CYAN if task.status == BG_RUNNING else GREEN
            marker = "◇" if task.status == BG_RUNNING else "●"
            status_label = task.status
            lines.append(
                f"  {status_color}{marker}{RESET} {task.task_id}  "
                f"{DIM}{desc}{RESET}  {status_color}{status_label}{RESET}"
            )
            # 增量输出：只渲染新增部分
            all_lines = task.output.splitlines()
            new_lines = all_lines[task.output_lines:]
            task.output_lines = len(all_lines)
            for line in new_lines:
                indent = "  " + " " * (len(task.task_id) + 3)
                lines.append(f"{DIM}{indent}{line}{RESET}")
        return "\n".join(lines)


# ─── 渐变色工具函数 ───────────────────────────────────────────────────────────


def gradient_text(
    text: str,
    start_rgb: tuple[int, int, int],
    end_rgb: tuple[int, int, int],
    mid_rgb: tuple[int, int, int] | None = None,
) -> str:
    """将 text 渲染为渐变色（ANSI 24-bit 真彩色）。"""
    r1, g1, b1 = start_rgb
    r2, g2, b2 = end_rgb
    length = len(text)
    result = []
    for i, ch in enumerate(text):
        if mid_rgb is None:
            ratio = i / max(length - 1, 1)
            r = int(r1 + (r2 - r1) * ratio)
            g = int(g1 + (g2 - g1) * ratio)
            b = int(b1 + (b2 - b1) * ratio)
        else:
            rm, gm, bm = mid_rgb
            pink_end = int(max(length - 1, 1) * 0.60)
            if i <= pink_end:
                ratio = i / max(pink_end, 1)
                r = int(r1 + (rm - r1) * ratio)
                g = int(g1 + (gm - g1) * ratio)
                b = int(b1 + (bm - b1) * ratio)
            else:
                ratio = (i - pink_end) / max(length - 1 - pink_end, 1)
                r = int(rm + (r2 - rm) * ratio)
                g = int(gm + (g2 - gm) * ratio)
                b = int(bm + (b2 - bm) * ratio)
        result.append(f"\033[38;2;{r};{g};{b}m{ch}")
    result.append(RESET)
    return "".join(result)


def rainbow_text(text: str) -> str:
    """将 text 渲染为彩虹色。"""
    result = []
    length = len(text)
    for i, ch in enumerate(text):
        hue = (i / max(length - 1, 1)) * 300
        r, g, b = _hsl_to_rgb(hue, 0.80, 0.65)
        result.append(f"\033[38;2;{r};{g};{b}m{ch}")
    result.append(RESET)
    return "".join(result)


def _hsl_to_rgb(h: float, s: float, l: float) -> tuple[int, int, int]:
    """HSL 颜色转换为 RGB。"""
    h = h / 360.0
    if s == 0:
        v = int(l * 255)
        return v, v, v
    def _hue2rgb(p, q, t):
        if t < 0: t += 1
        if t > 1: t -= 1
        if t < 1/6: return p + (q - p) * 6 * t
        if t < 1/2: return q
        if t < 2/3: return p + (q - p) * (2/3 - t) * 6
        return p
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q
    r = _hue2rgb(p, q, h + 1/3)
    g = _hue2rgb(p, q, h)
    b = _hue2rgb(p, q, h - 1/3)
    return int(r * 255), int(g * 255), int(b * 255)


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
  {CYAN}/clear{RESET}                  创建新 session
  {CYAN}/session <id>{RESET}           切换到已有 session
  {CYAN}/sessions{RESET}               列出所有 session
  {CYAN}/verbosity [level]{RESET}      查看或切换展示详细程度
                           levels: verbose（默认）| final_only
  {CYAN}/stream{RESET}                 切换 token 流开关（on/off）
  {CYAN}/interactive{RESET}            切换交互模式开关（on/off）
  {CYAN}/q{RESET} or {CYAN}exit{RESET}              退出

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


async def api_inject_answer(session_id: str, answer: str) -> None:
    """
    向 SSE 流注入 ask_user 问题的答案。

    Args:
        session_id: 当前会话 ID。
        answer:     用户回答的文本。
    """
    async with httpx.AsyncClient() as c:
        await c.post(
            f"{BASE_URL}/chat/stream/answer",
            json={"answer": answer},
            headers={"X-Session-Id": session_id},
            timeout=10,
        )


async def api_inject_permission(session_id: str, granted: bool) -> None:
    """
    向 SSE 流注入权限决定（批准/拒绝工具调用）。

    Args:
        session_id: 当前会话 ID。
        granted:    True=批准，False=拒绝。
    """
    async with httpx.AsyncClient() as c:
        await c.post(
            f"{BASE_URL}/chat/stream/permission",
            json={"granted": granted},
            headers={"X-Session-Id": session_id},
            timeout=10,
        )


async def api_chat_stream(
    client: httpx.AsyncClient,
    session_id: str,
    message: str,
    bg_tasks: BackgroundTaskManager,
    verbosity: str = "verbose",
    stream: bool = True,
    interactive: bool = True,
    stop_spinner: "callable | None" = None,
) -> str:
    """
    通过 SSE 流式接口发送消息，实时渲染 token 和后台任务。

    支持 ask_user（提问用户）和 permission_request（权限确认）的交互式应答：
    - ask_user：打印问题，从 stdin 读取答案，通过 /chat/stream/answer 注入
    - permission_request：打印工具名和参数，从 stdin 读取 y/n，通过 /chat/stream/permission 注入

    Args:
        client:        httpx.AsyncClient 实例。
        session_id:    当前会话 ID。
        message:       用户输入。
        bg_tasks:      BackgroundTaskManager 实例，事件处理器会更新它。
        verbosity:     展示详细程度（verbose/final_only）。
        stream:        是否推 token 流。
        interactive:   是否等待用户交互。
        stop_spinner:  第一个可见事件到来时调用，用于停止外部 spinner。

    Returns:
        最终的回复文本（用于显示摘要）。
    """
    final_text = ""
    tool_result_buffer: list[str] = []

    def _stop_spinner_once():
        """调用外部 stop_spinner 回调，且只调用一次。"""
        nonlocal stop_spinner
        if stop_spinner is not None:
            stop_spinner()
            stop_spinner = None  # 置空，后续不再调用

    request_body: dict = {"message": message, "verbosity": verbosity, "stream": stream, "interactive": interactive}

    async with client.stream(
        "POST",
        f"{BASE_URL}/chat/stream",
        json=request_body,
        headers={"X-Session-Id": session_id},
        timeout=900,
    ) as resp:
        if resp.status_code != 200:
            text = await resp.text()
            raise httpx.HTTPStatusError(text, response=resp)

        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            raw = line[6:].strip()
            if not raw:
                continue

            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            t = event.get("type", "")

            # ── 后台任务事件 ─────────────────────────────────────────────────
            if t == "task_started":
                bg_tasks.on_task_started(event)
                bg_lines = bg_tasks.render()
                if bg_lines:
                    print(f"\n{bg_lines}", flush=True)
                continue

            if t == "task_progress":
                bg_tasks.on_task_progress(event)
                bg_lines = bg_tasks.render()
                if bg_lines:
                    # 覆盖刷新任务行
                    sys.stdout.write(f"\033[{bg_lines.count(chr(10)) + 1}A")
                    sys.stdout.write("\033[K")
                    print(bg_lines, flush=True)
                continue

            if t == "task_done":
                bg_tasks.on_task_done(event)
                # 打印一行完成信息
                status = event.get("status", "completed")
                status_color = GREEN if status in (BG_COMPLETED,) else RED
                print(
                    f"\n  {GREEN}●{RESET} {event.get('task_id')}  "
                    f"{status_color}{status}{RESET}",
                    flush=True
                )
                continue

            # ── 普通事件 ───────────────────────────────────────────────────
            if t == "token":
                _stop_spinner_once()
                sys.stdout.write(event.get("content", ""))
                sys.stdout.flush()
                final_text += event.get("content", "")

            elif t == "tool_start":
                _stop_spinner_once()
                tool = event.get("tool", "")
                preview = event.get("preview", "")
                if tool.startswith("mcp__"):
                    print(f"\n{GREEN}⏺ {tool}{RESET}", flush=True)
                    for part in preview.split(", "):
                        print(f"  {DIM}{part}{RESET}", flush=True)
                else:
                    print(
                        f"\n{GREEN}⏺ {tool.capitalize()}{RESET}"
                        f"({DIM}{preview}{RESET})",
                        flush=True,
                    )

            elif t == "tool_result":
                output = event.get("output", "")
                if output:
                    print(f"{DIM}  → {output[:200]}{RESET}", flush=True)
                tool_result_buffer.append(output)

            elif t == "done":
                final_text = event.get("content", "") or final_text
                print(flush=True)

            elif t == "error":
                print(f"\n{RED}⏺ Error: {event.get('message', '')}{RESET}", flush=True)

            elif t == "compact":
                print(f"\n{YELLOW}[compact: {event.get('reason', '')}]{RESET}", flush=True)

            elif t == "ask_user":
                # 打印 Agent 提出的问题和选项，读取用户输入后注入答案
                print(flush=True)
                questions = event.get("questions", [])
                for q in questions:
                    print(f"\n{CYAN}? {q.get('question', '')}{RESET}", flush=True)
                    for i, opt in enumerate(q.get("options", []), 1):
                        label = opt.get("label", "")
                        desc = opt.get("description", "")
                        print(f"  {i}. {label} — {DIM}{desc}{RESET}", flush=True)
                sys.stdout.write(f"{BOLD}Your answer: {RESET}")
                sys.stdout.flush()
                answer = sys.stdin.readline().strip()
                # 在后台协程中注入答案，不阻塞当前 SSE 读循环
                asyncio.ensure_future(api_inject_answer(session_id, answer))

            elif t == "permission_request":
                # 打印工具名和参数，读取 y/n 后注入权限决定
                tool_name = event.get("tool", "")
                tool_input = event.get("input", {})
                print(f"\n{YELLOW}⚠ Permission request: {BOLD}{tool_name}{RESET}", flush=True)
                # 打印工具参数（最多显示200字）
                import json as _json
                input_preview = _json.dumps(tool_input, ensure_ascii=False)[:200]
                print(f"  {DIM}{input_preview}{RESET}", flush=True)
                sys.stdout.write(f"{BOLD}Allow? [y/N]: {RESET}")
                sys.stdout.flush()
                choice = sys.stdin.readline().strip().lower()
                granted = choice in ("y", "yes")
                asyncio.ensure_future(api_inject_permission(session_id, granted))
                status = f"{GREEN}granted{RESET}" if granted else f"{RED}denied{RESET}"
                print(f"  → {status}", flush=True)

    return final_text


# ─── 主循环 ───────────────────────────────────────────────────────────────────


async def tui_main():
    # 华为芭乐效果
    _BALE_START = (255, 105, 180)
    _BALE_MID   = (255, 255, 255)
    _BALE_END   = (0,   200, 83)

    print()
    for line in LOGO.strip("\n").splitlines():
        print(gradient_text(f"  {line}", _BALE_START, _BALE_END, _BALE_MID))
    slogan = "  powered by multi-provider LLM"
    print(f"  {rainbow_text(slogan)}{RESET}")
    print(f"{DIM}  SSE TUI — backend: {BASE_URL}{RESET}\n")

    http = httpx.Client(timeout=900)

    try:
        session = api_create_session(http)
    except Exception as e:
        print(f"{RED}⏺ 无法连接到后端 {BASE_URL}: {e}{RESET}")
        print(f"{DIM}请先启动 server.py: python server.py{RESET}")
        return

    session_id = session["id"]

    # 三个独立的输出控制参数（通过 slash 命令修改）
    current_verbosity: str = "verbose"
    current_stream: bool = True
    current_interactive: bool = True

    # 当前正在运行的 SSE task，供 ESC 中断使用
    _running_task: asyncio.Task | None = None

    def _status_line() -> str:
        s = "on" if current_stream else "off"
        i = "on" if current_interactive else "off"
        return f"verbosity: {current_verbosity} | stream: {s} | interactive: {i}"

    # slash 命令列表，用于 / 前缀补全
    _SLASH_COMMANDS = [
        "/clear", "/session", "/sessions",
        "/verbosity", "/stream", "/interactive",
        "/status", "/help", "/q",
    ]

    # ESC 键绑定：取消当前正在运行的 SSE task
    _kb = KeyBindings()

    @_kb.add("escape")
    def _on_escape(event):
        """ESC：如果有正在运行的 SSE task，取消它；否则清空当前输入行。"""
        if _running_task is not None and not _running_task.done():
            _running_task.cancel()
        else:
            event.current_buffer.reset()

    _completer = WordCompleter(_SLASH_COMMANDS, pattern=r"\/\S*", sentence=True)

    _pt_session = PromptSession(
        [("class:prompt", "✏️  ")],
        style=PTStyle.from_dict({"prompt": "bold ansiblue"}),
        key_bindings=_kb,
        completer=_completer,
        complete_while_typing=True,
    )

    print(
        f"{BOLD}CCServer SSE TUI{RESET} | {DIM}session: {session_id[:8]}{RESET}\n"
        f"{DIM}{_status_line()}{RESET}\n"
        f"Type {CYAN}/help{RESET} for commands.\n"
    )

    while True:
        try:
            print(separator())
            user_input = (await _pt_session.prompt_async()).strip()

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

            if user_input.startswith("/verbosity"):
                parts = user_input.split(None, 1)
                if len(parts) == 1:
                    print(f"{DIM}verbosity: {RESET}{CYAN}{current_verbosity}{RESET}  {DIM}可选: verbose | final_only{RESET}")
                else:
                    v = parts[1].strip()
                    if v in VALID_VERBOSITY:
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
                    f"{BOLD}Session:{RESET}     {DIM}{session_id}{RESET}\n"
                    f"{BOLD}Backend:{RESET}     {DIM}{BASE_URL}{RESET}\n"
                    f"{BOLD}Verbosity:{RESET}   {CYAN}{current_verbosity}{RESET}\n"
                    f"{BOLD}Stream:{RESET}      {CYAN}{'on' if current_stream else 'off'}{RESET}\n"
                    f"{BOLD}Interactive:{RESET} {CYAN}{'on' if current_interactive else 'off'}{RESET}"
                )
                continue

            # ── SSE 流式对话 ───────────────────────────────────────────────
            stop_event = threading.Event()
            spinner = threading.Thread(target=thinking_spinner, args=(stop_event,), daemon=True)
            spinner.start()
            bg_tasks = BackgroundTaskManager()

            def _do_stop_spinner():
                """停止 spinner 并等待线程结束，确保 \r 清屏完成后再输出内容。"""
                if not stop_event.is_set():
                    stop_event.set()
                    spinner.join()

            try:
                async def run_stream():
                    async with httpx.AsyncClient() as client:
                        return await api_chat_stream(
                            client,
                            session_id,
                            user_input,
                            bg_tasks,
                            verbosity=current_verbosity,
                            stream=current_stream,
                            interactive=current_interactive,
                            stop_spinner=_do_stop_spinner,
                        )

                _running_task = asyncio.create_task(run_stream())
                await _running_task
            except asyncio.CancelledError:
                print(f"\n{YELLOW}⏹ 已中断{RESET}")
            except httpx.HTTPStatusError as e:
                print(f"\n{RED}⏺ HTTP {e.response.status_code}: {e.response.text}{RESET}")
            except Exception as err:
                print(f"\n{RED}⏺ Error: {err}{RESET}")
            finally:
                _running_task = None
                stop_event.set()
                spinner.join()

        except (KeyboardInterrupt, EOFError):
            print()
            break
        except Exception as err:
            print(f"{RED}⏺ Error: {err}{RESET}")

    http.close()


def main():
    asyncio.run(tui_main())


if __name__ == "__main__":
    main()
