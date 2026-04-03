"""
hook_loader — 扫描 .ccserver/hooks/ 目录，加载并执行 Hook。

支持三种写法：

  风格 A：文件名 = 事件别名，文件内定义同名异步函数
    .ccserver/hooks/pre_message.py
        async def pre_message(message, ctx): ...

  风格 B：文件名任意，文件内定义多个事件函数（函数名 = 事件别名）
    .ccserver/hooks/my_plugin.py
        async def pre_message(message, ctx): ...
        async def tool_call_before(tool_name, tool_input, ctx): ...

  风格 C：Claude Code 兼容独立脚本，文件内有 def main()
    .ccserver/hooks/pre_context_inject.py
        # event: message:inbound:received
        def main():
            event = json.loads(sys.stdin.read())
            print(json.dumps({"hookSpecificOutput": {"additionalContext": "..."}}))
            sys.exit(0)  # exit 2 = block

识别逻辑（互斥三路）：
  1. 文件名 stem 匹配已知事件别名 → 风格 A
  2. 否则用 ast 检测是否有 def main() → 风格 C（不 import，避免 sys.exit）
  3. 否则 → 风格 B（import 后扫描所有与事件别名同名的函数）

事件名（冒号格式）与别名（下划线格式）映射：
  message:inbound:received  ↔  pre_message
  prompt:llm:output         ↔  post_message
  tool:call:before          ↔  tool_call_before
  ...（见 KNOWN_EVENTS）

发现路径（按优先级从高到低）：
  {project_root}/.ccserver/hooks/
  ~/.ccserver/hooks/
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import inspect
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from loguru import logger


# ── 事件定义 ─────────────────────────────────────────────────────────────────

# 标准事件名（冒号格式） → 别名（下划线格式，也是风格A/B的函数名）
# 执行模式：modifying = 串行，结果会影响流程；observing = 并行，结果忽略
KNOWN_EVENTS: dict[str, dict] = {
    "message:inbound:received": {"alias": "pre_message",          "mode": "modifying"},
    "prompt:llm:output":        {"alias": "post_message",         "mode": "observing"},
    "agent:stop":               {"alias": "agent_stop",           "mode": "observing"},
    "tool:call:before":         {"alias": "tool_call_before",     "mode": "modifying"},
    "tool:call:after":          {"alias": "tool_call_after",      "mode": "observing"},
    "tool:call:failure":        {"alias": "tool_call_failure",    "mode": "observing"},
    "session:start":            {"alias": "session_start",        "mode": "observing"},
    "session:end":              {"alias": "session_end",          "mode": "observing"},
    "subagent:spawning":        {"alias": "subagent_spawning",    "mode": "observing"},
    "subagent:ended":           {"alias": "subagent_ended",       "mode": "observing"},
    "agent:compact:before":     {"alias": "agent_compact_before", "mode": "observing"},
    "agent:compact:after":      {"alias": "agent_compact_after",  "mode": "observing"},
    "agent:limit":              {"alias": "agent_limit",          "mode": "observing"},
}

# 别名 → 标准事件名（方便反查）
_ALIAS_TO_EVENT: dict[str, str] = {
    info["alias"]: event for event, info in KNOWN_EVENTS.items()
}

# 所有合法的别名集合（用于快速查找）
_ALL_ALIASES: set[str] = set(_ALIAS_TO_EVENT.keys())


# ── 数据结构 ──────────────────────────────────────────────────────────────────


@dataclass
class HookContext:
    """
    Hook 执行时注入的上下文信息，包含当前代理和会话的基本状态。
    """
    session_id: str
    workdir: Path
    project_root: Path
    depth: int            # 0 = 根代理，>0 = 子代理
    agent_id: str
    agent_name: str | None
    is_orchestrator: bool = False   # depth == 0 时为 True，由 _build_hook_ctx 填入


@dataclass
class HookResult:
    """
    modifying 类型事件的返回值。observing 事件不需要返回 HookResult。

    message:inbound:received 用：
      message            — 替换用户消息内容（None = 不修改）
      additional_context — 追加给模型的额外上下文

    tool:call:before 用：
      block        — True 则阻断工具调用
      block_reason — 阻断原因，作为工具错误结果返回给 LLM
    """
    message: str | None = None
    additional_context: str | None = None
    block: bool = False
    block_reason: str = ""


@dataclass
class HookEntry:
    """一个已注册的 hook handler。"""
    event: str           # 标准事件名（冒号格式）
    style: str           # "A" | "B" | "C"
    fn: Callable | None  # 风格A/B：异步函数；风格C：None
    script: Path | None  # 风格C：脚本路径；风格A/B：None
    location: Path       # 来源文件路径（用于日志）


# ── 辅助函数 ──────────────────────────────────────────────────────────────────


def _has_main_function(py_file: Path) -> bool:
    """
    用 ast 静态检测文件是否包含 def main()。
    不 import 文件，避免风格C脚本里的 sys.exit(0) 在加载时执行。
    """
    try:
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                return True
        return False
    except Exception as e:
        logger.warning("ast.parse failed | path={} error={}", py_file, e)
        return False


def _read_event_from_comment(py_file: Path) -> str | None:
    """
    从文件头部注释中读取事件声明，格式：# event: <event_name>
    支持标准事件名（冒号）或别名（下划线）。
    """
    try:
        with open(py_file, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= 20:  # 只看前 20 行
                    break
                line = line.strip()
                if line.startswith("# event:"):
                    value = line[len("# event:"):].strip()
                    # 支持别名
                    if value in _ALIAS_TO_EVENT:
                        return _ALIAS_TO_EVENT[value]
                    # 支持标准名
                    if value in KNOWN_EVENTS:
                        return value
                    logger.warning("Unknown event in comment | value={} path={}", value, py_file)
                    return None
    except Exception as e:
        logger.warning("Failed to read event comment | path={} error={}", py_file, e)
    return None


def _import_module(py_file: Path) -> object | None:
    """动态 import 一个 .py 文件，返回模块对象，失败返回 None。"""
    try:
        module_name = f"_ccserver_hook_{py_file.stem}_{id(py_file)}"
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    except Exception as e:
        logger.error("Failed to import hook file | path={} error={}", py_file, e)
        return None


def _merge_results(base: HookResult, new_result) -> HookResult:
    """
    合并两个 HookResult。聚合规则：
      block              — first-wins（base 已为 True 则保持）
      block_reason       — 跟随第一个 block=True 的值
      message            — last-wins（新值覆盖旧值，None 不覆盖）
      additional_context — concat（换行拼接，None 跳过）
    """
    if new_result is None:
        return base

    # 如果返回值是字符串（pre_message 风格A/B 直接返回 str 也合法）
    if isinstance(new_result, str):
        new_result = HookResult(message=new_result)

    if not isinstance(new_result, HookResult):
        return base

    merged = HookResult(
        message=base.message,
        additional_context=base.additional_context,
        block=base.block,
        block_reason=base.block_reason,
    )

    # block: first-wins
    if not merged.block and new_result.block:
        merged.block = True
        merged.block_reason = new_result.block_reason

    # message: last-wins（新值非 None 时才覆盖）
    if new_result.message is not None:
        merged.message = new_result.message

    # additional_context: concat
    if new_result.additional_context:
        if merged.additional_context:
            merged.additional_context = merged.additional_context + "\n" + new_result.additional_context
        else:
            merged.additional_context = new_result.additional_context

    return merged


def _parse_script_output(data: dict) -> HookResult:
    """
    解析风格C脚本的 stdout JSON，转换为 HookResult。
    兼容 Claude Code hookSpecificOutput 格式。
    """
    result = HookResult()

    hook_output = data.get("hookSpecificOutput", {})
    if hook_output:
        ctx = hook_output.get("additionalContext", "")
        if ctx:
            result.additional_context = ctx

    return result


# ── HookLoader ────────────────────────────────────────────────────────────────


class HookLoader:
    """
    扫描多个目录，加载所有 hook 文件，提供 emit / emit_void 执行接口。

    优先级：前面的目录优先（同事件名，先扫描到的排在前面先执行）。
    """

    def __init__(self, *hooks_dirs: Path):
        # 事件名 → handler 列表（按加载顺序）
        self._handlers: dict[str, list[HookEntry]] = {}
        for d in hooks_dirs:
            self._scan(d)

    @classmethod
    def from_workdir(cls, project_root: Path, global_config_dir: Path | None = None) -> "HookLoader":
        """根据项目根目录自动构建标准扫描路径。"""
        global_dir = global_config_dir or Path.home() / ".ccserver"
        return cls(
            project_root / ".ccserver" / "hooks",
            global_dir / "hooks",
        )

    # ── 扫描 ─────────────────────────────────────────────────────────────────

    def _scan(self, hooks_dir: Path):
        if not hooks_dir.exists():
            return
        for py_file in sorted(hooks_dir.glob("*.py")):
            self._load_file(py_file)

    def _load_file(self, py_file: Path):
        stem = py_file.stem

        # 风格A：文件名 stem 是已知别名
        if stem in _ALL_ALIASES:
            self._load_style_a(py_file, stem)
            return

        # 风格A：文件名 stem 是已知标准事件名（带冒号的不会是合法文件名，但兜底一下）
        if stem in KNOWN_EVENTS:
            self._load_style_a(py_file, stem)
            return

        # 风格C：文件内有 def main()
        if _has_main_function(py_file):
            self._load_style_c(py_file)
            return

        # 风格B：import 后扫描所有与别名同名的函数
        self._load_style_b(py_file)

    def _load_style_a(self, py_file: Path, name: str):
        """风格A：文件名即事件名/别名，取同名函数注册。"""
        # 转换为标准事件名
        if name in _ALIAS_TO_EVENT:
            event = _ALIAS_TO_EVENT[name]
        elif name in KNOWN_EVENTS:
            event = name
        else:
            return

        module = _import_module(py_file)
        if module is None:
            return

        # 函数名优先用别名，其次用标准名（去掉冒号，用下划线）
        alias = KNOWN_EVENTS[event]["alias"]
        fn = getattr(module, alias, None) or getattr(module, name, None)
        if fn is None or not callable(fn):
            logger.error("Style A: function '{}' not found | path={}", alias, py_file)
            return

        entry = HookEntry(event=event, style="A", fn=fn, script=None, location=py_file.resolve())
        self._register(entry)
        logger.debug("Hook loaded (A) | event={} path={}", event, py_file)

    def _load_style_b(self, py_file: Path):
        """风格B：扫描模块内所有与事件别名同名的函数，逐个注册。"""
        module = _import_module(py_file)
        if module is None:
            return

        found = 0
        for alias, event in _ALIAS_TO_EVENT.items():
            fn = getattr(module, alias, None)
            if fn is not None and callable(fn):
                entry = HookEntry(event=event, style="B", fn=fn, script=None, location=py_file.resolve())
                self._register(entry)
                found += 1
                logger.debug("Hook loaded (B) | event={} path={}", event, py_file)

        if found == 0:
            logger.debug("Style B: no event functions found | path={}", py_file)

    def _load_style_c(self, py_file: Path):
        """风格C：独立脚本，从头部注释读取 event 声明。"""
        event = _read_event_from_comment(py_file)
        if event is None:
            logger.warning("Style C: no '# event: ...' comment found, skipping | path={}", py_file)
            return

        entry = HookEntry(event=event, style="C", fn=None, script=py_file.resolve(), location=py_file.resolve())
        self._register(entry)
        logger.debug("Hook loaded (C) | event={} path={}", event, py_file)

    def _register(self, entry: HookEntry):
        if entry.event not in self._handlers:
            self._handlers[entry.event] = []
        self._handlers[entry.event].append(entry)

    # ── 执行 ─────────────────────────────────────────────────────────────────

    async def emit(self, event: str, *args, ctx: HookContext) -> HookResult:
        """
        触发 modifying 类型事件。串行执行所有 handler，合并返回值。
        block=True 时短路停止。
        """
        entries = self._handlers.get(event, [])
        if not entries:
            return HookResult()

        result = HookResult()
        for entry in entries:
            try:
                raw = await self._call_entry(entry, *args, ctx=ctx)
                result = _merge_results(result, raw)
                if result.block:
                    break
            except Exception as e:
                logger.error("Hook error (emit) | event={} path={} error={}", event, entry.location, e)

        return result

    async def emit_void(self, event: str, *args, ctx: HookContext) -> None:
        """
        触发 observing 类型事件。并行执行所有 handler，忽略返回值。
        单个 handler 出错不影响其他。
        """
        entries = self._handlers.get(event, [])
        if not entries:
            return

        tasks = []
        for entry in entries:
            tasks.append(self._safe_call(entry, *args, ctx=ctx))
        await asyncio.gather(*tasks)

    async def _safe_call(self, entry: HookEntry, *args, ctx: HookContext):
        """包装 _call_entry，捕获所有异常用于 emit_void 的并行执行。"""
        try:
            await self._call_entry(entry, *args, ctx=ctx)
        except Exception as e:
            logger.error("Hook error (emit_void) | event={} path={} error={}", entry.event, entry.location, e)

    async def _call_entry(self, entry: HookEntry, *args, ctx: HookContext):
        """根据风格调用 handler，返回原始结果。"""
        if entry.style in ("A", "B"):
            # 检查函数是否接受 ctx 参数（兼容旧签名）
            sig = inspect.signature(entry.fn)
            params = list(sig.parameters.keys())
            if "ctx" in params:
                return await entry.fn(*args, ctx=ctx)
            else:
                # 旧签名兼容：context dict
                old_ctx = {
                    "session_id": ctx.session_id,
                    "workdir": ctx.workdir,
                    "depth": ctx.depth,
                }
                return await entry.fn(*args, old_ctx)
        else:
            # 风格C：spawn 子进程
            payload = self._build_payload(entry.event, *args, ctx=ctx)
            return await self._run_script(entry, payload)

    def _build_payload(self, event: str, *args, ctx: HookContext) -> dict:
        """构造风格C脚本的 stdin JSON payload。"""
        payload = {
            "hook_event_name": event,
            "session_id": ctx.session_id,
            "cwd": str(ctx.workdir),
            "project_root": str(ctx.project_root),
            "depth": ctx.depth,
            "is_orchestrator": ctx.is_orchestrator,
            "agent_id": ctx.agent_id,
            "agent_name": ctx.agent_name,
        }

        # 按事件类型追加专属字段
        alias = KNOWN_EVENTS.get(event, {}).get("alias", "")

        if alias == "pre_message" and args:
            payload["prompt"] = args[0]
        elif alias == "post_message" and args:
            payload["reply"] = args[0]
        elif alias == "agent_stop" and args:
            payload["reply"] = args[0]
        elif alias == "tool_call_before" and len(args) >= 2:
            payload["tool_name"] = args[0]
            payload["tool_input"] = args[1]
        elif alias == "tool_call_after" and len(args) >= 2:
            payload["tool_name"] = args[0]
            payload["result"] = args[1]
        elif alias == "tool_call_failure" and len(args) >= 2:
            payload["tool_name"] = args[0]
            payload["error"] = args[1]
        elif alias == "subagent_ended" and args:
            payload["summary"] = args[0]
        elif alias == "agent_limit" and args:
            payload["last_text"] = args[0]

        return payload

    async def _run_script(self, entry: HookEntry, payload: dict) -> HookResult:
        """spawn 子进程执行风格C脚本，解析 stdout JSON 返回 HookResult。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(entry.script),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as e:
            logger.error("Failed to spawn hook script | path={} error={}", entry.script, e)
            return HookResult()

        try:
            stdin_data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(stdin_data), timeout=30
            )
        except asyncio.TimeoutError:
            proc.kill()
            logger.error("Hook script timed out | path={}", entry.script)
            return HookResult()
        except Exception as e:
            logger.error("Hook script communication error | path={} error={}", entry.script, e)
            return HookResult()

        # exit code 2 = block
        if proc.returncode == 2:
            reason = stderr_bytes.decode("utf-8", errors="replace").strip()
            logger.debug("Hook script blocked | path={} reason={}", entry.script, reason)
            return HookResult(block=True, block_reason=reason)

        # 其他非零 = 非阻断错误，只记录日志
        if proc.returncode != 0:
            err = stderr_bytes.decode("utf-8", errors="replace").strip()
            logger.warning("Hook script non-zero exit | path={} code={} stderr={}", entry.script, proc.returncode, err)
            return HookResult()

        # 解析 stdout JSON
        stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
        if not stdout_text:
            return HookResult()

        try:
            data = json.loads(stdout_text)
            return _parse_script_output(data)
        except Exception as e:
            logger.warning("Hook script output not valid JSON | path={} error={}", entry.script, e)
            return HookResult()

    # ── 旧接口兼容 ────────────────────────────────────────────────────────────

    def get(self, name: str) -> HookEntry | None:
        """按事件名或别名查询，返回第一个 handler（旧接口兼容）。"""
        event = _ALIAS_TO_EVENT.get(name) or (name if name in KNOWN_EVENTS else None)
        if event is None:
            return None
        entries = self._handlers.get(event, [])
        return entries[0] if entries else None

    async def call_pre_message(self, message: str, context: dict) -> str:
        """旧接口兼容：内部委托给 emit()。"""
        ctx = HookContext(
            session_id=context.get("session_id", ""),
            workdir=context.get("workdir", Path(".")),
            project_root=context.get("workdir", Path(".")),
            depth=context.get("depth", 0),
            agent_id="",
            agent_name=None,
        )
        result = await self.emit("message:inbound:received", message, ctx=ctx)
        if result.message is not None:
            return result.message
        return message

    async def call_post_message(self, reply: str, context: dict) -> None:
        """旧接口兼容：内部委托给 emit_void()。"""
        ctx = HookContext(
            session_id=context.get("session_id", ""),
            workdir=context.get("workdir", Path(".")),
            project_root=context.get("workdir", Path(".")),
            depth=context.get("depth", 0),
            agent_id="",
            agent_name=None,
        )
        await self.emit_void("prompt:llm:output", reply, ctx=ctx)
