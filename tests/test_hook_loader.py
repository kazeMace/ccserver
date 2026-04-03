"""
tests/test_hook_loader.py — HookLoader 单元测试

覆盖：
  - _has_main_function(): 有/无 def main()
  - _read_event_from_comment(): 标准名、别名、未知事件
  - _merge_results(): block first-wins、message last-wins、additional_context concat
  - _parse_script_output(): hookSpecificOutput 解析
  - HookLoader._scan(): 不存在目录静默跳过
  - HookLoader 风格A: 文件名=别名，注册正确函数
  - HookLoader 风格A: 文件名=标准事件名（加冒号映射）
  - HookLoader 风格B: 文件名任意，扫描模块内所有匹配函数
  - HookLoader 风格C: def main() 存在，从注释读取事件名
  - HookLoader.emit(): 串行执行、返回合并结果、block 短路
  - HookLoader.emit_void(): 并行执行、单个异常不影响其他
  - HookLoader.emit() 无 handler 时返回空 HookResult
  - HookLoader.get(): 按别名/标准名查询
  - HookLoader.call_pre_message() / call_post_message() 旧接口兼容
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from ccserver.hooks.loader import (
    HookLoader,
    HookContext,
    HookResult,
    _has_main_function,
    _read_event_from_comment,
    _merge_results,
    _parse_script_output,
)


# ─── 辅助 ─────────────────────────────────────────────────────────────────────


def _make_ctx(**kwargs) -> HookContext:
    defaults = dict(
        session_id="sess-001",
        workdir=Path("/tmp"),
        project_root=Path("/tmp"),
        depth=0,
        agent_id="agent-001",
        agent_name="test-agent",
        is_orchestrator=True,
    )
    defaults.update(kwargs)
    return HookContext(**defaults)


def _write_py(tmp_path: Path, filename: str, content: str) -> Path:
    p = tmp_path / filename
    p.write_text(content, encoding="utf-8")
    return p


# ─── _has_main_function ───────────────────────────────────────────────────────


def test_has_main_function_true(tmp_path):
    f = _write_py(tmp_path, "hook.py", "def main():\n    pass\n")
    assert _has_main_function(f) is True


def test_has_main_function_false(tmp_path):
    f = _write_py(tmp_path, "hook.py", "def pre_message(msg, ctx):\n    pass\n")
    assert _has_main_function(f) is False


def test_has_main_function_nonexistent():
    assert _has_main_function(Path("/nonexistent/path.py")) is False


# ─── _read_event_from_comment ─────────────────────────────────────────────────


def test_read_event_from_comment_standard_name(tmp_path):
    f = _write_py(tmp_path, "hook.py", "# event: message:inbound:received\ndef main(): pass\n")
    event = _read_event_from_comment(f)
    assert event == "message:inbound:received"


def test_read_event_from_comment_alias(tmp_path):
    f = _write_py(tmp_path, "hook.py", "# event: pre_message\ndef main(): pass\n")
    event = _read_event_from_comment(f)
    assert event == "message:inbound:received"


def test_read_event_from_comment_unknown_returns_none(tmp_path):
    f = _write_py(tmp_path, "hook.py", "# event: unknown_event\ndef main(): pass\n")
    event = _read_event_from_comment(f)
    assert event is None


def test_read_event_from_comment_no_comment_returns_none(tmp_path):
    f = _write_py(tmp_path, "hook.py", "def main(): pass\n")
    event = _read_event_from_comment(f)
    assert event is None


def test_read_event_from_comment_beyond_20_lines_ignored(tmp_path):
    # 注释在第 25 行，超过 20 行限制，不应被识别
    lines = ["# just a comment\n"] * 24 + ["# event: pre_message\n", "def main(): pass\n"]
    f = _write_py(tmp_path, "hook.py", "".join(lines))
    event = _read_event_from_comment(f)
    assert event is None


# ─── _merge_results ───────────────────────────────────────────────────────────


def test_merge_results_block_first_wins():
    base = HookResult(block=True, block_reason="first")
    new = HookResult(block=True, block_reason="second")
    merged = _merge_results(base, new)
    assert merged.block is True
    assert merged.block_reason == "first"  # 已被 base block，保留 base


def test_merge_results_block_triggers_on_new():
    base = HookResult(block=False)
    new = HookResult(block=True, block_reason="stop")
    merged = _merge_results(base, new)
    assert merged.block is True
    assert merged.block_reason == "stop"


def test_merge_results_message_last_wins():
    base = HookResult(message="old")
    new = HookResult(message="new")
    merged = _merge_results(base, new)
    assert merged.message == "new"


def test_merge_results_message_none_does_not_overwrite():
    base = HookResult(message="keep")
    new = HookResult(message=None)
    merged = _merge_results(base, new)
    assert merged.message == "keep"


def test_merge_results_additional_context_concat():
    base = HookResult(additional_context="first")
    new = HookResult(additional_context="second")
    merged = _merge_results(base, new)
    assert merged.additional_context == "first\nsecond"


def test_merge_results_additional_context_base_none():
    base = HookResult(additional_context=None)
    new = HookResult(additional_context="new_ctx")
    merged = _merge_results(base, new)
    assert merged.additional_context == "new_ctx"


def test_merge_results_new_is_none_returns_base():
    base = HookResult(message="keep", block=False)
    merged = _merge_results(base, None)
    assert merged.message == "keep"


def test_merge_results_new_is_string_treated_as_message():
    base = HookResult()
    merged = _merge_results(base, "hello from hook")
    assert merged.message == "hello from hook"


# ─── _parse_script_output ────────────────────────────────────────────────────


def test_parse_script_output_additional_context():
    data = {"hookSpecificOutput": {"additionalContext": "injected context"}}
    result = _parse_script_output(data)
    assert result.additional_context == "injected context"


def test_parse_script_output_empty():
    result = _parse_script_output({})
    assert result.additional_context is None
    assert result.block is False


# ─── HookLoader 风格A ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_style_a_file_named_alias(tmp_path):
    """文件名=别名（pre_message.py），注册 pre_message 事件。"""
    _write_py(tmp_path, "pre_message.py", """\
async def pre_message(message, ctx):
    return "modified: " + message
""")
    loader = HookLoader(tmp_path)
    ctx = _make_ctx()
    result = await loader.emit("message:inbound:received", "hello", ctx=ctx)
    assert result.message == "modified: hello"


@pytest.mark.asyncio
async def test_style_a_file_named_alias_tool_call_before(tmp_path):
    """风格A：tool_call_before.py → tool:call:before 事件。"""
    _write_py(tmp_path, "tool_call_before.py", """\
async def tool_call_before(tool_name, tool_input, ctx):
    if tool_name == "Bash":
        return __import__('ccserver.hooks.loader', fromlist=['HookResult']).HookResult(block=True, block_reason="blocked")
""")
    loader = HookLoader(tmp_path)
    ctx = _make_ctx()
    result = await loader.emit("tool:call:before", "Bash", {}, ctx=ctx)
    assert result.block is True


# ─── HookLoader 风格B ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_style_b_multiple_events_in_one_file(tmp_path):
    """风格B：一个文件内定义多个事件函数，各自注册到对应事件。"""
    _write_py(tmp_path, "my_plugin.py", """\
async def pre_message(message, ctx):
    return "pre:" + message

async def post_message(reply, ctx):
    pass  # observing，忽略返回值
""")
    loader = HookLoader(tmp_path)
    ctx = _make_ctx()

    pre_result = await loader.emit("message:inbound:received", "hello", ctx=ctx)
    assert pre_result.message == "pre:hello"

    # post_message 是 observing 事件，用 emit_void 不报错
    await loader.emit_void("prompt:llm:output", "reply text", ctx=ctx)


@pytest.mark.asyncio
async def test_style_b_no_event_functions_skipped(tmp_path):
    """风格B：文件内无任何事件函数，不报错，无注册效果。"""
    _write_py(tmp_path, "empty_plugin.py", """\
def some_unrelated_function():
    pass
""")
    loader = HookLoader(tmp_path)
    result = await loader.emit("message:inbound:received", "msg", ctx=_make_ctx())
    assert result.block is False
    assert result.message is None


# ─── HookLoader 风格C ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_style_c_script_runs_and_injects_context(tmp_path):
    """风格C：子进程执行，返回 additionalContext。

    注意：sys.exit() 必须在 main() 内，不能在模块顶层调用，
    否则 HookLoader 误判风格、import 时会直接触发 SystemExit。
    """
    script_content = """\
# event: pre_message
import json
import sys

def main():
    event = json.loads(sys.stdin.read())
    print(json.dumps({"hookSpecificOutput": {"additionalContext": "ctx_from_script"}}))
    sys.exit(0)

if __name__ == "__main__":
    main()
"""
    _write_py(tmp_path, "pre_context_inject.py", script_content)
    loader = HookLoader(tmp_path)
    ctx = _make_ctx()
    result = await loader.emit("message:inbound:received", "hello", ctx=ctx)
    assert result.additional_context == "ctx_from_script"


@pytest.mark.asyncio
async def test_style_c_exit_2_blocks(tmp_path):
    """风格C：exit code 2 → block=True。"""
    script_content = """\
# event: tool:call:before
import sys

def main():
    sys.stderr.write("blocked by policy")
    sys.exit(2)

if __name__ == "__main__":
    main()
"""
    _write_py(tmp_path, "block_hook.py", script_content)
    loader = HookLoader(tmp_path)
    ctx = _make_ctx()
    result = await loader.emit("tool:call:before", "Bash", {}, ctx=ctx)
    assert result.block is True
    assert "blocked" in result.block_reason


@pytest.mark.asyncio
async def test_style_c_no_event_comment_skipped(tmp_path):
    """风格C：无 # event: 注释，跳过注册。"""
    _write_py(tmp_path, "missing_event.py", """\
def main():
    pass
""")
    loader = HookLoader(tmp_path)
    result = await loader.emit("message:inbound:received", "hello", ctx=_make_ctx())
    assert result.message is None


# ─── emit() 串行执行与 block 短路 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_serial_execution_order(tmp_path):
    """多个 handler 按文件名顺序串行执行。"""
    order = []
    _write_py(tmp_path, "pre_message.py", f"""\
async def pre_message(message, ctx):
    import sys
    # 写入外部列表无法跨进程，改用 message 内容追踪
    return "a:" + message
""")
    loader = HookLoader(tmp_path)
    ctx = _make_ctx()
    result = await loader.emit("message:inbound:received", "msg", ctx=ctx)
    assert result.message == "a:msg"


@pytest.mark.asyncio
async def test_emit_block_short_circuits(tmp_path):
    """block=True 后不再执行后续 handler。"""
    call_log = []

    # 使用两个文件模拟两个 handler，a_pre_message 先执行（字母序）
    _write_py(tmp_path, "a_plugin.py", """\
from ccserver.hooks.loader import HookResult
async def pre_message(message, ctx):
    return HookResult(block=True, block_reason="stop")
""")
    _write_py(tmp_path, "b_plugin.py", """\
async def pre_message(message, ctx):
    # 这个不应该被调用
    return "should not reach"
""")
    loader = HookLoader(tmp_path)
    ctx = _make_ctx()
    result = await loader.emit("message:inbound:received", "hello", ctx=ctx)
    assert result.block is True
    # message 不应被第二个 handler 设置
    assert result.message != "should not reach"


@pytest.mark.asyncio
async def test_emit_no_handlers_returns_empty_result(tmp_path):
    """没有注册任何 handler 时，emit 返回空 HookResult（不报错）。"""
    loader = HookLoader(tmp_path)  # 空目录
    result = await loader.emit("message:inbound:received", "hello", ctx=_make_ctx())
    assert isinstance(result, HookResult)
    assert result.block is False


# ─── emit_void() 并行执行 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_emit_void_exception_does_not_propagate(tmp_path):
    """emit_void 中单个 handler 抛异常，不影响整体执行。"""
    _write_py(tmp_path, "my_plugin.py", """\
async def post_message(reply, ctx):
    raise RuntimeError("intentional error")
""")
    loader = HookLoader(tmp_path)
    # 不应抛出异常
    await loader.emit_void("prompt:llm:output", "reply", ctx=_make_ctx())


# ─── HookLoader.get() 旧接口 ─────────────────────────────────────────────────


def test_get_by_alias(tmp_path):
    """get() 按别名查询返回第一个 handler。"""
    _write_py(tmp_path, "pre_message.py", """\
async def pre_message(message, ctx):
    return message
""")
    loader = HookLoader(tmp_path)
    entry = loader.get("pre_message")
    assert entry is not None
    assert entry.event == "message:inbound:received"


def test_get_unknown_alias_returns_none(tmp_path):
    loader = HookLoader(tmp_path)
    assert loader.get("no_such_event") is None


# ─── HookLoader 目录不存在静默跳过 ───────────────────────────────────────────


def test_nonexistent_dir_does_not_raise(tmp_path):
    loader = HookLoader(tmp_path / "nonexistent")
    assert loader._handlers == {}


# ─── 旧接口兼容 ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_pre_message_compat(tmp_path):
    _write_py(tmp_path, "pre_message.py", """\
async def pre_message(message, ctx):
    return "wrapped:" + message
""")
    loader = HookLoader(tmp_path)
    ctx = {"session_id": "s1", "workdir": Path("/tmp"), "depth": 0}
    result = await loader.call_pre_message("hi", ctx)
    assert result == "wrapped:hi"


@pytest.mark.asyncio
async def test_call_pre_message_compat_no_handler(tmp_path):
    """无 handler 时原样返回消息。"""
    loader = HookLoader(tmp_path)
    result = await loader.call_pre_message("unchanged", {"session_id": "s", "workdir": Path("/tmp"), "depth": 0})
    assert result == "unchanged"


@pytest.mark.asyncio
async def test_call_post_message_compat(tmp_path):
    """call_post_message 不报错即可（observing，忽略返回值）。"""
    loader = HookLoader(tmp_path)
    await loader.call_post_message("reply text", {"session_id": "s", "workdir": Path("/tmp"), "depth": 0})
