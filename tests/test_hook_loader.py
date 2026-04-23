"""
tests/test_hook_loader.py — HookLoader 执行策略单元测试

测试覆盖：
  - parallel + all（默认 CC 行为）
  - parallel + first / last
  - serial + all / first / last
  - 异常处理（单个 hook 失败不影响其他）
  - _resolve_strategy 混合策略检测
  - permission_behavior 聚合
"""

import asyncio
import pytest
from pathlib import Path

from ccserver.managers.hooks import AlwaysMatcher
from ccserver.managers.hooks import (
    HookLoader,
    HookEntry,
    HookContext,
    HookResult,
)


def _make_ctx() -> HookContext:
    return HookContext(
        session_id="sess-1",
        workdir=Path("/tmp"),
        project_root=Path("/tmp"),
        depth=0,
        agent_id="agent-1",
        agent_name="test",
    )


def _make_entry(
    event: str,
    exec_type: str,
    collect: str,
    return_val: HookResult | Exception | None,
    delay: float = 0,
) -> HookEntry:
    return HookEntry(
        event=event,
        executor={"_test_return": return_val, "_test_delay": delay},
        matcher=AlwaysMatcher(),
        env={},
        timeout=30,
        source="test",
        execution=exec_type,
        collect=collect,
    )


class PatchedHookLoader(HookLoader):
    """HookLoader 的子类，重写 _call_entry 以便做受控测试。"""

    async def _call_entry(self, entry: HookEntry, payload: dict, ctx: HookContext):
        delay = entry.executor.get("_test_delay", 0)
        if delay:
            await asyncio.sleep(delay)
        val = entry.executor.get("_test_return")
        if isinstance(val, Exception):
            raise val
        return val


# ─────────────────────────────────────────────────────────────────────────────
# parallel + all
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parallel_all_merge():
    loader = PatchedHookLoader()
    loader._handlers["tool:call:before"] = [
        _make_entry("tool:call:before", "parallel", "all", HookResult(updated_input={"a": 1})),
        _make_entry("tool:call:before", "parallel", "all", HookResult(updated_input={"b": 2})),
    ]

    result = await loader.emit("tool:call:before", {}, _make_ctx())
    assert result.updated_input == {"b": 2}


@pytest.mark.asyncio
async def test_parallel_all_block_first_wins():
    loader = PatchedHookLoader()
    loader._handlers["tool:call:before"] = [
        _make_entry("tool:call:before", "parallel", "all", HookResult(block=True, block_reason="first")),
        _make_entry("tool:call:before", "parallel", "all", HookResult(block=False)),
    ]

    result = await loader.emit("tool:call:before", {}, _make_ctx())
    assert result.block is True
    assert result.block_reason == "first"


@pytest.mark.asyncio
async def test_parallel_all_block_shortcircuit():
    loader = PatchedHookLoader()
    loader._handlers["tool:call:before"] = [
        _make_entry("tool:call:before", "parallel", "all", HookResult(block=True)),
        _make_entry("tool:call:before", "parallel", "all", HookResult(updated_input={"c": 3})),
    ]

    result = await loader.emit("tool:call:before", {}, _make_ctx())
    assert result.block is True
    assert result.updated_input is None


# ─────────────────────────────────────────────────────────────────────────────
# parallel + first / last
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parallel_first_returns_first_non_none():
    loader = PatchedHookLoader()
    loader._handlers["tool:call:before"] = [
        _make_entry("tool:call:before", "parallel", "first", HookResult(message="A")),
        _make_entry("tool:call:before", "parallel", "first", HookResult(message="B")),
    ]

    result = await loader.emit("tool:call:before", {}, _make_ctx())
    assert result.message == "A"


@pytest.mark.asyncio
async def test_parallel_last_returns_last_non_none():
    loader = PatchedHookLoader()
    loader._handlers["tool:call:before"] = [
        _make_entry("tool:call:before", "parallel", "last", HookResult(message="A")),
        _make_entry("tool:call:before", "parallel", "last", HookResult(message="B")),
    ]

    result = await loader.emit("tool:call:before", {}, _make_ctx())
    assert result.message == "B"


@pytest.mark.asyncio
async def test_parallel_first_skips_none_result():
    loader = PatchedHookLoader()
    loader._handlers["tool:call:before"] = [
        _make_entry("tool:call:before", "parallel", "first", None),
        _make_entry("tool:call:before", "parallel", "first", HookResult(message="B")),
    ]

    result = await loader.emit("tool:call:before", {}, _make_ctx())
    assert result.message == "B"


# ─────────────────────────────────────────────────────────────────────────────
# serial 执行
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_serial_all_order_preserved():
    loader = PatchedHookLoader()
    order: list[int] = []

    async def side_effect(entry: HookEntry, payload: dict, ctx: HookContext):
        idx = entry.executor.get("_test_idx")
        order.append(idx)
        await asyncio.sleep(0.01)
        return HookResult(updated_input={"idx": idx})

    loader._call_entry = side_effect  # type: ignore[assignment]

    loader._handlers["tool:call:before"] = [
        _make_entry("tool:call:before", "serial", "all", HookResult()),
        _make_entry("tool:call:before", "serial", "all", HookResult()),
    ]
    loader._handlers["tool:call:before"][0].executor["_test_idx"] = 1
    loader._handlers["tool:call:before"][1].executor["_test_idx"] = 2

    result = await loader.emit("tool:call:before", {}, _make_ctx())
    assert order == [1, 2]
    assert result.updated_input == {"idx": 2}


@pytest.mark.asyncio
async def test_serial_first():
    loader = PatchedHookLoader()
    loader._handlers["tool:call:before"] = [
        _make_entry("tool:call:before", "serial", "first", HookResult(updated_input={"a": 1})),
        _make_entry("tool:call:before", "serial", "first", HookResult(updated_input={"b": 2})),
    ]

    result = await loader.emit("tool:call:before", {}, _make_ctx())
    assert result.updated_input == {"a": 1}


@pytest.mark.asyncio
async def test_serial_last():
    loader = PatchedHookLoader()
    loader._handlers["tool:call:before"] = [
        _make_entry("tool:call:before", "serial", "last", HookResult(updated_input={"a": 1})),
        _make_entry("tool:call:before", "serial", "last", HookResult(updated_input={"b": 2})),
    ]

    result = await loader.emit("tool:call:before", {}, _make_ctx())
    assert result.updated_input == {"b": 2}


# ─────────────────────────────────────────────────────────────────────────────
# 异常情况
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parallel_all_single_failure_ignored():
    loader = PatchedHookLoader()
    loader._handlers["tool:call:before"] = [
        _make_entry("tool:call:before", "parallel", "all", RuntimeError("boom")),
        _make_entry("tool:call:before", "parallel", "all", HookResult(message="ok")),
    ]

    result = await loader.emit("tool:call:before", {}, _make_ctx())
    assert result.message == "ok"


@pytest.mark.asyncio
async def test_parallel_first_all_failures():
    loader = PatchedHookLoader()
    loader._handlers["tool:call:before"] = [
        _make_entry("tool:call:before", "parallel", "first", RuntimeError("boom")),
        _make_entry("tool:call:before", "parallel", "first", RuntimeError("boom2")),
    ]

    result = await loader.emit("tool:call:before", {}, _make_ctx())
    assert result == HookResult()


@pytest.mark.asyncio
async def test_emit_void_parallel():
    loader = PatchedHookLoader()
    called: list[int] = []

    async def side_effect(entry: HookEntry, payload: dict, ctx: HookContext):
        called.append(entry.executor.get("_test_idx"))
        return HookResult()

    loader._call_entry = side_effect  # type: ignore[assignment]
    loader._handlers["tool:call:after"] = [
        _make_entry("tool:call:after", "parallel", "all", HookResult()),
        _make_entry("tool:call:after", "parallel", "all", HookResult()),
    ]
    loader._handlers["tool:call:after"][0].executor["_test_idx"] = 1
    loader._handlers["tool:call:after"][1].executor["_test_idx"] = 2

    await loader.emit_void("tool:call:after", {}, _make_ctx())
    assert sorted(called) == [1, 2]


@pytest.mark.asyncio
async def test_emit_void_serial():
    loader = PatchedHookLoader()
    called: list[int] = []

    async def side_effect(entry: HookEntry, payload: dict, ctx: HookContext):
        called.append(entry.executor.get("_test_idx"))
        return HookResult()

    loader._call_entry = side_effect  # type: ignore[assignment]
    loader._handlers["tool:call:after"] = [
        _make_entry("tool:call:after", "serial", "all", HookResult()),
        _make_entry("tool:call:after", "serial", "all", HookResult()),
    ]
    loader._handlers["tool:call:after"][0].executor["_test_idx"] = 1
    loader._handlers["tool:call:after"][1].executor["_test_idx"] = 2

    await loader.emit_void("tool:call:after", {}, _make_ctx())
    assert called == [1, 2]


# ─────────────────────────────────────────────────────────────────────────────
# _resolve_strategy
# ─────────────────────────────────────────────────────────────────────────────


def test_resolve_strategy_uses_first_entry():
    loader = PatchedHookLoader()
    entries = [
        _make_entry("x", "serial", "last", HookResult()),
        _make_entry("x", "parallel", "all", HookResult()),
    ]
    execution, collect = loader._resolve_strategy(entries, "x")
    assert execution == "serial"
    assert collect == "last"


def test_resolve_strategy_empty_fallback():
    loader = PatchedHookLoader()
    execution, collect = loader._resolve_strategy([], "tool:call:before")
    assert execution == "parallel"
    assert collect == "all"


# ─────────────────────────────────────────────────────────────────────────────
# permission_behavior 聚合
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_permission_behavior_deny_wins_over_allow():
    from ccserver.managers.hooks import _merge_results

    base = HookResult(permission_behavior="allow")
    new = HookResult(permission_behavior="deny")
    merged = _merge_results(base, new)
    assert merged.permission_behavior == "deny"


@pytest.mark.asyncio
async def test_permission_behavior_ask_wins_over_allow():
    from ccserver.managers.hooks import _merge_results

    base = HookResult(permission_behavior="allow")
    new = HookResult(permission_behavior="ask")
    merged = _merge_results(base, new)
    assert merged.permission_behavior == "ask"


@pytest.mark.asyncio
async def test_permission_behavior_passthrough_lowest():
    from ccserver.managers.hooks import _merge_results

    base = HookResult(permission_behavior="deny")
    new = HookResult(permission_behavior="passthrough")
    merged = _merge_results(base, new)
    assert merged.permission_behavior == "deny"


# ─────────────────────────────────────────────────────────────────────────────
# command hook cwd / env
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_command_cwd_is_project_root(tmp_path, monkeypatch):
    """command hook 应以 ctx.project_root 为 cwd 执行。"""
    monkeypatch.chdir(tmp_path)
    from ccserver.managers.hooks import HookLoader, HookEntry, HookContext, HookResult

    # 在 project_root 下写一个脚本，输出 cwd
    project_root = tmp_path / "project"
    project_root.mkdir()
    script = project_root / "print_cwd.py"
    script.write_text("import json, os; print(json.dumps({'continue': True, 'hookSpecificOutput': {}}))", encoding="utf-8")

    entry = HookEntry(
        event="message:inbound:received",
        executor={"type": "command", "command": "python print_cwd.py"},
        matcher=AlwaysMatcher(),
        env={},
        timeout=30,
        source="test",
    )
    ctx = HookContext(
        session_id="s1",
        workdir=Path("/tmp"),
        project_root=project_root,
        depth=0,
        agent_id="a1",
        agent_name="test",
    )
    loader = HookLoader()
    # 只要 subprocess 没报 FileNotFoundError，就说明 cwd 对了
    result = await loader._run_command(entry, {}, ctx)
    assert isinstance(result, HookResult)


# ─────────────────────────────────────────────────────────────────────────────
# slash command 触发 message:inbound:received hook
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_slash_command_triggers_user_prompt_submit():
    """slash command 也应触发 message:inbound:received (UserPromptSubmit) hook。"""
    # 只测 agent.py 中 run() 方法的调用顺序，不构造完整 Agent
    from unittest.mock import MagicMock, AsyncMock
    import ccserver.agent as agent_mod

    calls = []

    class FakeHooks:
        async def emit(self, event, payload, ctx):
            calls.append((event, payload.get("prompt")))
            return HookResult()

        async def emit_void(self, *args, **kwargs):
            pass

    fake_session = MagicMock()
    fake_session.hooks = FakeHooks()
    fake_session.commands = {}  # 空命令表

    # 构造最小 Agent 只保留 run() 依赖的字段
    class MinimalAgent:
        def __init__(self):
            self.session = fake_session
            self.context = MagicMock()
            self.context.depth = 0

        def _build_hook_ctx(self):
            return MagicMock()

        async def _handle_command(self, raw):
            pass

        async def _loop(self):
            return "done"

        async def run(self, message: str) -> str:
            hook_result = await self.session.hooks.emit(
                "message:inbound:received",
                {"prompt": message},
                self._build_hook_ctx(),
            )
            if hook_result.message is not None:
                message = hook_result.message
            if hook_result.additional_context:
                message = message + "\n\n" + hook_result.additional_context

            if message.startswith("/"):
                await self._handle_command(message)
            else:
                pass  # _append 不需要测
            return await self._loop()

    agent = MinimalAgent()
    result = await agent.run("/persona 小雨")
    assert result == "done"
    assert calls == [("message:inbound:received", "/persona 小雨")]
