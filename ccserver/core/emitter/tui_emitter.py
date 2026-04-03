import asyncio
import itertools
import sys
import threading

from . import BaseEmitter

# ─── ANSI 颜色常量 ─────────────────────────────────────────────────────────────

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
BLUE, CYAN, GREEN, YELLOW, RED = (
    "\033[34m", "\033[36m", "\033[32m", "\033[33m", "\033[31m",
)


class TUIEmitter(BaseEmitter):
    """
    将代理事件渲染为彩色终端输出。
    继承 BaseEmitter 的 fmt_* 方法，只需实现 emit()。

    通过 set_spinner() 传入 Spinner，第一个 token 到来时自动停止转圈，
    避免 stdout 输出交错。
    """

    def __init__(self):
        self._spinner: "Spinner | None" = None

    def set_spinner(self, spinner: "Spinner"):
        self._spinner = spinner

    def _stop_spinner(self):
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None

    async def emit(self, event: dict) -> None:
        t = event["type"]
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
