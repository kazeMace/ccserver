"""
tests/test_agent_handle.py — BackgroundAgentHandle 生命周期与集成测试。

覆盖：
  - BackgroundAgentHandle.cancel() + agent_task_state.mark_cancelled 联动
  - register_handle / unregister_handle 全局注册表
  - is_running() 辅助方法
  - handle._task.done() 检测

注意：forward_agent_events / _poll_agent_progress / outbox 已在 EventBus 重构中删除，
对应的状态更新逻辑移入 agent.py 的 _forward_bus_events 闭包，
由 test_event_bus.py 覆盖总线行为，agent 集成测试覆盖完整流程。
"""

import asyncio
import pytest

from ccserver.agent_handle import BackgroundAgentHandle
from ccserver.agent_registry import (
    register_handle,
    unregister_handle,
    _HANDLE_REGISTRY,
)
from ccserver.tasks import AgentTaskState, AgentTaskStatus


# ─── register_handle / unregister_handle ──────────────────────────────────────


class TestHandleRegistry:
    """全局句柄注册表测试。"""

    def test_register_and_get(self):
        """注册后可通过 agent_id 查到 handle。"""
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
        state.mark_running()

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


# ─── is_running() ──────────────────────────────────────────────────────────────


class TestHandleIsRunning:
    """is_running() 辅助方法测试。"""

    @pytest.mark.anyio
    async def test_is_running_true_when_task_alive(self):
        """_task 未完成时 is_running() 返回 True。"""
        handle = BackgroundAgentHandle(
            agent_id="run-agent",
            task_id=None,
            agent_task_id="a00000r01",
        )

        async def keep_alive():
            await asyncio.sleep(60)

        handle._task = asyncio.create_task(keep_alive())
        try:
            assert handle.is_running() is True
        finally:
            handle._task.cancel()
            try:
                await handle._task
            except asyncio.CancelledError:
                pass

    @pytest.mark.anyio
    async def test_is_running_false_when_no_task(self):
        """_task 为 None 时 is_running() 返回 False。"""
        handle = BackgroundAgentHandle(
            agent_id="no-task-agent",
            task_id=None,
            agent_task_id="a00000r02",
        )
        assert handle.is_running() is False

    @pytest.mark.anyio
    async def test_is_running_false_after_task_done(self):
        """_task 已完成时 is_running() 返回 False。"""
        handle = BackgroundAgentHandle(
            agent_id="done-agent2",
            task_id=None,
            agent_task_id="a00000r03",
        )

        async def instant():
            pass

        handle._task = asyncio.create_task(instant())
        await handle._task  # 等待完成
        assert handle.is_running() is False


# ─── handle._task done 检测 ───────────────────────────────────────────────────


class TestHandleTaskDetection:
    """handle.state 和 _task.done() 状态检测。"""

    @pytest.mark.anyio
    async def test_task_not_done_while_running(self):
        """_task 未完成时，_task.done() 返回 False。"""
        handle = BackgroundAgentHandle(
            agent_id="run-agent2",
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
