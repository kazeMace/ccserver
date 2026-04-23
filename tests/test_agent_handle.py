"""
tests/test_agent_handle.py — BackgroundAgentHandle 生命周期与集成测试。

覆盖：
  - BackgroundAgentHandle.cancel() + agent_task_state.mark_cancelled 联动
  - register_handle / unregister_handle 全局注册表
  - forward_agent_events 对 agent_task_state 的状态更新
  - handle._task.done() 检测

注意：这些测试覆盖 agent_handle.py 中 test_agent_task.py 未覆盖的部分。
"""

import asyncio
import pytest

from ccserver.agent_handle import (
    BackgroundAgentHandle,
    forward_agent_events,
)
from ccserver.agent_registry import (
    register_handle,
    unregister_handle,
    _HANDLE_REGISTRY,
)
from ccserver.tasks import AgentTaskState, AgentTaskStatus


# ─── Mock parent emitter ──────────────────────────────────────────────────────


class MockEmitter:
    def __init__(self):
        self.progress_calls: list[dict] = []
        self.done_calls: list[dict] = []

    async def emit_task_progress(self, task_id, status, output="", progress=None):
        self.progress_calls.append({
            "task_id": task_id, "status": status, "output": output, "progress": progress,
        })

    async def emit_task_done(self, task_id, status, output="", exit_code=None, reason=None):
        self.done_calls.append({
            "task_id": task_id, "status": status, "output": output,
            "exit_code": exit_code, "reason": reason,
        })


# ─── register_handle / unregister_handle ──────────────────────────────────────


class TestHandleRegistry:
    """全局句柄注册表测试。"""

    def test_register_and_get(self):
        """注册后可通过 agent_id 查到 handle。"""
        from ccserver import agent_handle as ah

        handle = BackgroundAgentHandle(
            agent_id="reg-test-agent",
            task_id=None,
            agent_task_id="a00000reg",
        )
        register_handle(handle)

        try:
            found = _HANDLE_REGISTRY.get("reg-test-agent")
            assert found is handle
        finally:
            unregister_handle("reg-test-agent")

    def test_unregister_removes_handle(self):
        """unregister 后注册表中不再有该 handle。"""
        from ccserver import agent_handle as ah

        handle = BackgroundAgentHandle(
            agent_id="unreg-test-agent",
            task_id=None,
            agent_task_id="a00000unr",
        )
        register_handle(handle)
        unregister_handle("unreg-test-agent")

        assert _HANDLE_REGISTRY.get("unreg-test-agent") is None

    def test_register_idempotent_overwrites(self):
        """重复注册同一个 agent_id，后者覆盖前者。"""
        h1 = BackgroundAgentHandle(agent_id="dup-agent", task_id=None, agent_task_id="a00000dup1")
        h2 = BackgroundAgentHandle(agent_id="dup-agent", task_id=None, agent_task_id="a00000dup2")
        register_handle(h1)
        register_handle(h2)

        try:
            assert _HANDLE_REGISTRY.get("dup-agent") is h2
        finally:
            unregister_handle("dup-agent")


# ─── BackgroundAgentHandle.cancel() ──────────────────────────────────────────


class TestHandleCancel:
    """cancel() 方法测试。"""

    @pytest.mark.anyio
    async def test_cancel_calls_agent_task_state_mark_cancelled(self):
        """
        cancel() 时若有 agent_task_state，
        应调用其 mark_cancelled() 并更新 phase 为 cancelled。
        """
        state = AgentTaskState(id="a00000c01", agent_id="cancel-agent")
        state.mark_running()  # 模拟 agent 正在运行

        class MockState:
            phase = "running"

        handle = BackgroundAgentHandle(
            agent_id="cancel-agent",
            task_id=None,
            agent_task_id="a00000c01",
            state=MockState(),
            agent_task_state=state,
        )

        async def never_ends():
            await asyncio.sleep(999)

        handle._task = asyncio.create_task(never_ends())
        await handle.cancel()

        # agent_task_state 状态应为 cancelled
        assert state.status == AgentTaskStatus.CANCELLED
        # handle.state.phase 也应更新
        assert handle.state.phase == "cancelled"

    @pytest.mark.anyio
    async def test_cancel_idempotent_when_already_done(self):
        """
        task 已结束时调用 cancel() 不应崩溃。
        """
        handle = BackgroundAgentHandle(
            agent_id="done-agent",
            task_id=None,
            agent_task_id="a00000d01",
        )
        handle._task = None  # 已结束，无 task

        # 不应抛异常
        await handle.cancel()


# ─── forward_agent_events + agent_task_state ───────────────────────────────────


class TestForwardAgentEventsWithState:
    """forward_agent_events 对 agent_task_state 的状态更新。"""

    @pytest.mark.anyio
    async def test_done_updates_agent_task_state(self):
        """
        outbox 收到 done 时，forward_agent_events 应：
        1. 调用 agent_task_state.mark_completed(result=...)
        2. 推送 emit_task_done(status=completed)
        """
        outbox: asyncio.Queue = asyncio.Queue()
        await outbox.put({"type": "done", "content": "analysis complete"})

        state = AgentTaskState(id="a00000s01", agent_id="state-agent")
        state.mark_running()

        handle = BackgroundAgentHandle(
            agent_id="state-agent",
            task_id=None,
            agent_task_id="a00000s01",
            outbox=outbox,
            agent_task_state=state,
        )
        emitter = MockEmitter()

        await forward_agent_events(handle, emitter)

        # agent_task_state 更新为 completed
        assert state.status == AgentTaskStatus.COMPLETED
        assert state.result == "analysis complete"

        # emitter 收到 task_done
        assert len(emitter.done_calls) == 1
        assert emitter.done_calls[0]["status"] == "completed"
        assert emitter.done_calls[0]["output"] == "analysis complete"

    @pytest.mark.anyio
    async def test_error_updates_agent_task_state(self):
        """
        outbox 收到 error 时，应更新 agent_task_state 为 failed，
        并推送 emit_task_done(status=failed)。
        """
        outbox: asyncio.Queue = asyncio.Queue()
        await outbox.put({"type": "error", "error": "LLM timeout"})

        state = AgentTaskState(id="a00000s02", agent_id="err-agent")
        state.mark_running()

        handle = BackgroundAgentHandle(
            agent_id="err-agent",
            task_id=None,
            agent_task_id="a00000s02",
            outbox=outbox,
            agent_task_state=state,
        )
        emitter = MockEmitter()

        await forward_agent_events(handle, emitter)

        assert state.status == AgentTaskStatus.FAILED
        assert state.error == "LLM timeout"
        assert emitter.done_calls[0]["status"] == "failed"
        assert "LLM timeout" in emitter.done_calls[0]["reason"]

    @pytest.mark.anyio
    async def test_cancelled_updates_agent_task_state(self):
        """
        outbox 收到 cancelled 时，应更新 agent_task_state 为 cancelled。
        """
        outbox: asyncio.Queue = asyncio.Queue()
        await outbox.put({"type": "cancelled"})

        state = AgentTaskState(id="a00000s03", agent_id="can-agent")
        state.mark_running()

        handle = BackgroundAgentHandle(
            agent_id="can-agent",
            task_id=None,
            agent_task_id="a00000s03",
            outbox=outbox,
            agent_task_state=state,
        )
        emitter = MockEmitter()

        await forward_agent_events(handle, emitter)

        assert state.status == AgentTaskStatus.CANCELLED
        assert emitter.done_calls[0]["status"] == "cancelled"


# ─── handle._task done 检测 ───────────────────────────────────────────────────


class TestHandleTaskDetection:
    """handle.state 和 _task.done() 状态检测。"""

    @pytest.mark.anyio
    async def test_has_running_returns_true_when_task_running(self):
        """_task 未完成时，_task.done() 返回 False。"""
        handle = BackgroundAgentHandle(
            agent_id="run-agent",
            task_id=None,
            agent_task_id="a00000t01",
        )

        async def keep_alive():
            await asyncio.sleep(60)

        handle._task = asyncio.create_task(keep_alive())
        try:
            assert handle._task.done() is False
        finally:
            handle._task.cancel()
            try:
                await handle._task
            except asyncio.CancelledError:
                pass

    @pytest.mark.anyio
    async def test_state_none_does_not_break_cancel(self):
        """
        handle.state = None 时 cancel() 不应崩溃。
        """
        handle = BackgroundAgentHandle(
            agent_id="nostate-agent",
            task_id=None,
            agent_task_id="a00000n01",
            state=None,
        )

        async def never():
            await asyncio.sleep(999)

        handle._task = asyncio.create_task(never())
        await handle.cancel()  # 不应抛异常
        assert handle._task.done()
