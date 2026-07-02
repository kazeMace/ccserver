"""
tests/test_agent_task.py — AgentTaskState 与 AgentTaskRegistry 单元测试。

覆盖：
  - generate_agent_id() 唯一性与前缀
  - AgentTaskState 状态流转（pending → running → completed/failed/cancelled）
  - to_dict / from_dict 序列化
  - AgentTaskRegistry 注册/查询/终止/驱逐/summary
  - BackgroundAgentHandle cancel / send_message / is_running

注意：forward_agent_events / outbox / _wait_done 已在 EventBus 重构中删除，
原有测试移入 test_agent_handle.py 和 test_event_bus.py 覆盖。
"""

import asyncio
import pytest

from ccserver.tasks import (
    AgentTaskState,
    AgentTaskRegistry,
    AgentTaskStatus,
    generate_agent_id,
    AGENT_TASK_PREFIX,
)
from ccserver.agent_handle import BackgroundAgentHandle


# ─── ID 生成 ──────────────────────────────────────────────────────────────────

class TestGenerateAgentId:
    """ID 生成测试（同步，不需要事件循环）。"""

    def test_id_starts_with_a(self):
        """ID 必须以 'a' 前缀开头，对应 local_agent 类型。"""
        tid = generate_agent_id()
        assert tid.startswith(AGENT_TASK_PREFIX)

    def test_id_length(self):
        """ID 长度为 9 位：1 位前缀 + 8 位 hex。"""
        tid = generate_agent_id()
        assert len(tid) == 9

    def test_id_unique(self):
        """两次调用应生成不同的 UUID，不会碰撞。"""
        ids = {generate_agent_id() for _ in range(100)}
        assert len(ids) == 100, "100 次调用应产生 100 个不同 ID"


# ─── AgentTaskState 状态机 ─────────────────────────────────────────────────────

class TestAgentTaskStateLifecycle:
    """AgentTaskState 状态流转测试。"""

    @pytest.mark.anyio
    async def test_initial_pending(self):
        """初始状态必须为 pending。"""
        state = AgentTaskState(
            id="a00000001",
            agent_id="agent-001",
            prompt="hello",
        )
        assert state.status == AgentTaskStatus.PENDING
        assert state.is_running is False
        assert state.is_done is False

    @pytest.mark.anyio
    async def test_mark_running_sets_start_time(self):
        """mark_running 应设置 start_time。"""
        state = AgentTaskState(id="a00000001", agent_id="agent-001")
        state.mark_running()
        assert state.status == AgentTaskStatus.RUNNING
        assert state.start_time is not None

    @pytest.mark.anyio
    async def test_mark_running_wrong_state_warns_not_raises(self):
        """非 pending 状态调用 mark_running 应记录 warning 但不崩溃（P0-3 soft 处理）。"""
        state = AgentTaskState(id="a00000001", agent_id="agent-001")
        state.mark_running()
        # 第二次 mark_running（RUNNING → RUNNING）：记录 warning，但不抛异常
        state.mark_running()  # should NOT raise
        assert state.status == AgentTaskStatus.RUNNING

    @pytest.mark.anyio
    async def test_mark_completed_wrong_state_warns_not_raises(self):
        """非 running 状态调用 mark_completed 应记录 warning 但不崩溃（P0-3 soft 处理）。"""
        state = AgentTaskState(id="a00000001", agent_id="agent-001")
        # PENDING → mark_completed：记录 warning，但不抛异常
        state.mark_completed(result="early done")  # should NOT raise
        assert state.status == AgentTaskStatus.COMPLETED
        assert state.result == "early done"

    @pytest.mark.anyio
    async def test_teammate_multi_task_state_cycle(self):
        """Teammate 多任务场景：完成一个任务后状态重置为 RUNNING 再完成第二个任务（P0-5）。"""
        state = AgentTaskState(id="a00000001", agent_id="agent-001")
        # 第一个任务
        state.mark_running()
        state.mark_completed(result="task1 done")
        assert state.status == AgentTaskStatus.COMPLETED
        # spawn_manager 会在新任务前重置状态
        state.status = AgentTaskStatus.RUNNING
        state.result = None
        # 第二个任务
        state.mark_completed(result="task2 done")
        assert state.status == AgentTaskStatus.COMPLETED
        assert state.result == "task2 done"

    @pytest.mark.anyio
    async def test_mark_failed_sets_error_and_end_time(self):
        """mark_failed 应设置 error 和 end_time。"""
        state = AgentTaskState(id="a00000001", agent_id="agent-001")
        state.mark_running()
        state.mark_failed(error="something went wrong")
        assert state.status == AgentTaskStatus.FAILED
        assert state.error == "something went wrong"
        assert state.end_time is not None

    @pytest.mark.anyio
    async def test_mark_cancelled_sets_end_time(self):
        """mark_cancelled 应设置 end_time，状态为 CANCELLED。"""
        state = AgentTaskState(id="a00000001", agent_id="agent-001")
        state.mark_running()
        state.mark_cancelled()
        assert state.status == AgentTaskStatus.CANCELLED
        assert state.end_time is not None


# ─── AgentTaskRegistry ──────────────────────────────────────────────────────────

class TestAgentTaskRegistry:
    """AgentTaskRegistry 注册表测试。"""

    @pytest.mark.anyio
    async def test_register_and_get(self):
        """注册后可通过 get() 查询。"""
        registry = AgentTaskRegistry()
        state = AgentTaskState(id="a00000001", agent_id="agent-001")
        registry.register(state)
        assert registry.get("a00000001") is state

    @pytest.mark.anyio
    async def test_register_duplicate_raises(self):
        """重复注册相同 ID 应抛出 ValueError。"""
        registry = AgentTaskRegistry()
        state = AgentTaskState(id="a00000001", agent_id="agent-001")
        registry.register(state)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(state)

    @pytest.mark.anyio
    async def test_get_by_agent_id(self):
        """get_by_agent_id 可通过 agent_id 查找。"""
        registry = AgentTaskRegistry()
        state = AgentTaskState(id="a00000001", agent_id="agent-001")
        registry.register(state)
        found = registry.get_by_agent_id("agent-001")
        assert found is state

    @pytest.mark.anyio
    async def test_list_running_filters_correctly(self):
        """list_running 只返回 running 状态的任务。"""
        registry = AgentTaskRegistry()
        s1 = AgentTaskState(id="a00000001", agent_id="a1")
        s2 = AgentTaskState(id="a00000002", agent_id="a2")
        s1.mark_running()
        registry.register(s1)
        registry.register(s2)
        running = registry.list_running()
        assert len(running) == 1
        assert running[0] is s1

    @pytest.mark.anyio
    async def test_evict_requires_done_state(self):
        """驱逐非 done 状态任务应返回 False。"""
        registry = AgentTaskRegistry()
        state = AgentTaskState(id="a00000001", agent_id="agent-001")
        state.mark_running()
        registry.register(state)
        assert registry.evict("a00000001") is False
        assert registry.get("a00000001") is state

    @pytest.mark.anyio
    async def test_evict_done_tasks_batch(self):
        """evict_done_tasks 应批量驱逐所有已完成任务。"""
        registry = AgentTaskRegistry()
        s1 = AgentTaskState(id="a00000001", agent_id="a1")
        s2 = AgentTaskState(id="a00000002", agent_id="a2")
        s1.mark_running()
        s2.mark_running()
        s1.mark_completed(result="ok")
        s2.mark_completed(result="ok")
        registry.register(s1)
        registry.register(s2)
        n = registry.evict_done_tasks()
        assert n == 2
        assert registry.count() == 0

    @pytest.mark.anyio
    async def test_summary_counts(self):
        """summary 应正确统计各状态任务数量。"""
        registry = AgentTaskRegistry()
        s1 = AgentTaskState(id="a00000001", agent_id="a1")
        s2 = AgentTaskState(id="a00000002", agent_id="a2")
        s3 = AgentTaskState(id="a00000003", agent_id="a3")
        s1.mark_running()
        s2.mark_running()
        s2.mark_completed(result="ok")
        s3.mark_running()
        s3.mark_failed(error="err")
        registry.register(s1)
        registry.register(s2)
        registry.register(s3)
        summary = registry.summary()
        assert summary["total"] == 3
        assert summary["running"] == 1
        assert summary["completed"] == 1
        assert summary["failed"] == 1


# ─── 序列化 ───────────────────────────────────────────────────────────────────

class TestAgentTaskStateSerialization:
    """AgentTaskState to_dict / from_dict 测试。"""

    @pytest.mark.anyio
    async def test_to_dict_fields(self):
        """to_dict 应包含所有关键字段。"""
        state = AgentTaskState(
            id="a00000001",
            agent_id="agent-001",
            agent_name="coder",
            description="Write code",
            prompt="hello",
            status=AgentTaskStatus.COMPLETED,
            result="code written",
        )
        d = state.to_dict()
        assert d["id"] == "a00000001"
        assert d["agent_id"] == "agent-001"
        assert d["agent_name"] == "coder"
        assert d["description"] == "Write code"
        assert d["prompt"] == "hello"
        assert d["status"] == AgentTaskStatus.COMPLETED
        assert d["result"] == "code written"

    @pytest.mark.anyio
    async def test_from_dict_restores_fields(self):
        """from_dict 应完整恢复字段。"""
        data = {
            "id": "a00000001",
            "agent_id": "agent-001",
            "agent_name": "coder",
            "description": "Write code",
            "prompt": "hello",
            "status": AgentTaskStatus.FAILED,
            "error": "oops",
        }
        state = AgentTaskState.from_dict(data)
        assert state.id == "a00000001"
        assert state.agent_id == "agent-001"
        assert state.agent_name == "coder"
        assert state.description == "Write code"
        assert state.status == AgentTaskStatus.FAILED
        assert state.error == "oops"
        assert isinstance(state.inbox, asyncio.Queue)


# ─── BackgroundAgentHandle ────────────────────────────────────────────────────

class TestBackgroundAgentHandle:
    """BackgroundAgentHandle 基础操作测试。"""

    @pytest.mark.anyio
    async def test_send_message_injects_into_inbox(self):
        """send_message 应将消息注入 inbox。"""
        handle = BackgroundAgentHandle(
            agent_id="agent-001",
            task_id=None,
            agent_task_id="a00000001",
        )
        await handle.send_message({"type": "ping", "data": 42})
        msg = await asyncio.wait_for(handle.inbox.get(), timeout=1.0)
        assert msg == {"type": "ping", "data": 42}

    @pytest.mark.anyio
    async def test_cancel_sets_phase_to_cancelled(self):
        """cancel() 应将 state.phase 设为 cancelled。"""
        from ccserver.agent import AgentState
        state = AgentState(phase="running")
        handle = BackgroundAgentHandle(
            agent_id="agent-001",
            task_id=None,
            agent_task_id="a00000001",
            state=state,
        )

        async def dummy():
            await asyncio.sleep(10)

        handle._task = asyncio.create_task(dummy())
        await handle.cancel()
        assert state.phase == "cancelled"

    @pytest.mark.anyio
    async def test_is_running_true_when_task_alive(self):
        """_task 未完成时 is_running() 应返回 True。"""
        handle = BackgroundAgentHandle(
            agent_id="agent-002",
            task_id=None,
            agent_task_id="a00000002",
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
        """_task 为 None 时 is_running() 应返回 False。"""
        handle = BackgroundAgentHandle(
            agent_id="agent-003",
            task_id=None,
            agent_task_id="a00000003",
        )
        assert handle.is_running() is False
