import asyncio
import itertools
import sys
import threading
from dataclasses import dataclass, field

from .base import BaseEmitter

# ─── ANSI 颜色常量 ─────────────────────────────────────────────────────────────

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
BLUE, CYAN, GREEN, YELLOW, RED = (
    "\033[34m", "\033[36m", "\033[32m", "\033[33m", "\033[31m",
)


# ─── 背景任务状态 ──────────────────────────────────────────────────────────────

BG_RUNNING = "running"
BG_DONE    = "done"


@dataclass
class BGRunningTask:
    """单个后台任务的渲染状态。"""
    task_id: str
    task_type: str        # "local_bash" | "local_agent"
    description: str
    output: str = ""      # 累积的增量输出（用于追加渲染）
    output_lines: int = 0 # 已渲染的输出行数（用于追加新行）


# ─── 渐变色工具函数 ───────────────────────────────────────────────────────────


def gradient_text(text: str, start_rgb: tuple[int, int, int], end_rgb: tuple[int, int, int]) -> str:
    """
    将 text 渲染为从 start_rgb 到 end_rgb 的渐变色。

    使用 ANSI 24-bit 真彩色（\\033[38;2;R;G;B;m），每个字符单独计算 RGB 插值。
    支持纯文本、ASCII art、空格等所有字符。

    Args:
        text:       要渲染的文字。
        start_rgb:  起始颜色，RGB 三元组，如 (59, 130, 246) = #3B82F6。
        end_rgb:    终止颜色，RGB 三元组，如 (139, 92, 246) = #8B5CF6。

    Returns:
        带 ANSI 渐变色的字符串，末尾自动加上 RESET。
    """
    r1, g1, b1 = start_rgb
    r2, g2, b2 = end_rgb
    length = len(text)
    result = []

    for i, ch in enumerate(text):
        ratio = i / max(length - 1, 1)  # 避免 length=1 时除零
        r = int(r1 + (r2 - r1) * ratio)
        g = int(g1 + (g2 - g1) * ratio)
        b = int(b1 + (b2 - b1) * ratio)
        result.append(f"\033[38;2;{r};{g};{b}m{ch}")

    result.append(RESET)
    return "".join(result)


def rainbow_text(text: str) -> str:
    """
    将 text 渲染为彩虹色（HSL 绕色相环插值）。

    比 RGB 线性插值更鲜艳，适合短标语或装饰文字。

    Args:
        text: 要渲染的文字。

    Returns:
        带 ANSI 彩虹色的字符串，末尾自动加上 RESET。
    """
    import math

    result = []
    length = len(text)

    for i, ch in enumerate(text):
        # HSL: hue 从 0° 绕到 300°（红→橙→黄→绿→蓝→紫），s=80%, l=65%
        hue = (i / max(length - 1, 1)) * 300
        r, g, b = _hsl_to_rgb(hue, 0.80, 0.65)
        result.append(f"\033[38;2;{r};{g};{b}m{ch}")

    result.append(RESET)
    return "".join(result)


def _hsl_to_rgb(h: float, s: float, l: float) -> tuple[int, int, int]:
    """将 HSL 颜色转换为 RGB（h 为角度 0-360）。"""
    h = h / 360.0
    if s == 0:
        v = int(l * 255)
        return v, v, v

    def _hue2rgb(p: float, q: float, t: float) -> float:
        if t < 0:
            t += 1
        if t > 1:
            t -= 1
        if t < 1 / 6:
            return p + (q - p) * 6 * t
        if t < 1 / 2:
            return q
        if t < 2 / 3:
            return p + (q - p) * (2 / 3 - t) * 6
        return p

    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q
    r = _hue2rgb(p, q, h + 1 / 3)
    g = _hue2rgb(p, q, h)
    b = _hue2rgb(p, q, h - 1 / 3)
    return int(r * 255), int(g * 255), int(b * 255)


class TUIEmitter(BaseEmitter):
    """
    将代理事件渲染为彩色终端输出。
    继承 BaseEmitter 的 fmt_* 方法，只需实现 emit()。

    通过 set_spinner() 传入 Spinner，第一个 token 到来时自动停止转圈，
    避免 stdout 输出交错。
    """

    def __init__(self):
        self._spinner: "Spinner | None" = None
        # 后台任务状态字典：task_id → BGRunningTask
        self._bg_tasks: dict[str, BGRunningTask] = {}

    def set_spinner(self, spinner: "Spinner"):
        self._spinner = spinner

    def _stop_spinner(self):
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None

    # ── 后台任务渲染 ──────────────────────────────────────────────────────────

    def _render_task_bar(self, task: BGRunningTask) -> str:
        """
        渲染一行任务状态条。
        格式：◇ b3f2a1c0  npm run build         running
              │          │                        │
              marker     description             status
        """
        desc = task.description[:40]
        status = f"{CYAN}{BG_RUNNING}{RESET}"
        return f"  {CYAN}◇{RESET} {task.task_id}  {desc}  {status}"

    def _render_new_output_lines(self, task: BGRunningTask) -> str:
        """
        只渲染增量输出行（在已渲染行之后追加）。
        利用 task.output_lines 追踪已渲染行数，只输出新增部分。
        """
        all_lines = task.output.splitlines()
        new_lines = all_lines[task.output_lines:]
        task.output_lines = len(all_lines)
        if not new_lines:
            return ""
        indent = "  " + " " * (len(task.task_id) + 3)
        rendered = "\n".join(f"{DIM}{indent}{line}{RESET}" for line in new_lines)
        return rendered

    def _render_done_bar(self, task: BGRunningTask) -> str:
        """渲染已完成的任务行（替换 running 状态）。"""
        desc = task.description[:40]
        return f"  {GREEN}●{RESET} {task.task_id}  {desc}  {GREEN}{BG_DONE}{RESET}"

    # ── task 事件处理 ────────────────────────────────────────────────────────

    def _handle_task_started(self, event: dict) -> None:
        """处理 task_started 事件：注册任务并渲染任务行。"""
        task_id = event["task_id"]
        if task_id in self._bg_tasks:
            return  # 已存在，跳过重复
        task = BGRunningTask(
            task_id=task_id,
            task_type=event.get("task_type", ""),
            description=event.get("description", ""),
        )
        self._bg_tasks[task_id] = task
        bar = self._render_task_bar(task)
        print(f"\n{bar}", flush=True)

    def _handle_task_progress(self, event: dict) -> None:
        """处理 task_progress 事件：追加新输出行。"""
        task_id = event["task_id"]
        task = self._bg_tasks.get(task_id)
        if task is None:
            return
        task.output += event.get("output", "")
        new_rendering = self._render_new_output_lines(task)
        if new_rendering:
            print(new_rendering, flush=True)

    def _handle_task_done(self, event: dict) -> None:
        """处理 task_done 事件：替换任务行为完成状态。"""
        task_id = event["task_id"]
        task = self._bg_tasks.get(task_id)
        if task is None:
            return
        status = event.get("status", "completed")
        # 清屏当前行（VT100 序列），打印完成行
        # 由于不确定当前光标位置，打印在下方更安全
        print(f"  {GREEN}●{RESET} {task_id}  {task.description[:40]}  {GREEN}{status}{RESET}", flush=True)
        del self._bg_tasks[task_id]

    async def emit(self, event: dict) -> None:
        t = event["type"]
        # ── 后台任务事件（优先处理，避免与 tool_result 输出混淆）────────────
        if t == "task_started":
            self._handle_task_started(event)
            return
        if t == "task_progress":
            self._handle_task_progress(event)
            return
        if t == "task_done":
            self._handle_task_done(event)
            return
        # ── 普通 agent 事件 ─────────────────────────────────────────────────
        if t == "token":
            self._stop_spinner()
            print(event["content"], end="", flush=True)
        elif t == "tool_start":
            tool = event["tool"]
            preview = event["preview"]
            if tool.startswith("mcp__"):
                # MCP 工具：工具名单独一行，参数每个换行缩进显示
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
            print(f"{DIM}  → {event['output'][:200]}{RESET}", flush=True)
        elif t == "subagent_done":
            print(f"\n{DIM}  ✓ subagent: {event['content'][:100]}{RESET}", flush=True)
        elif t == "done":
            print()  # 流式 token 结束后换行
        elif t == "compact":
            print(f"\n{YELLOW}[compact: {event['reason']}]{RESET}", flush=True)
        elif t == "ask_user":
            # TUI 模式：把问题打印到终端，直接读 stdin 作为答案
            self._stop_spinner()
            questions = event.get("questions", [])
            for q in questions:
                print(f"\n{CYAN}? {q.get('question', '')}{RESET}", flush=True)
                for i, opt in enumerate(q.get("options", []), 1):
                    print(f"  {i}. {opt.get('label', '')} — {opt.get('description', '')}", flush=True)
        elif t == "error":
            print(f"\n{RED}⏺ Error: {event['message']}{RESET}", flush=True)

    async def emit_ask_user(self, questions: list) -> str:
        """
        TUI 模式：打印问题到终端，从 stdin 读取用户输入作为答案。
        在事件循环中用 run_in_executor 避免阻塞。
        """
        await self.emit(self.fmt_ask_user(questions))

        # 打印输入提示
        print(f"\n{BOLD}Your answer (press Enter to submit):{RESET} ", end="", flush=True)

        # 在线程中读 stdin，不阻塞事件循环
        loop = asyncio.get_event_loop()
        answer = await loop.run_in_executor(None, sys.stdin.readline)
        return answer.strip()


class Spinner:
    FRAMES = ["🌍", "🌎", "🌏"]

    def __init__(self, label: str = "Thinking"):
        self.label = label
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        import time
        for frame in itertools.cycle(self.FRAMES):
            if self._stop.is_set():
                break
            sys.stdout.write(f"\r{CYAN}{frame}{RESET} {DIM}{self.label}...{RESET}")
            sys.stdout.flush()
            time.sleep(0.4)
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join()
