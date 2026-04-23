"""
tests/test_agent_task_progress.py — Agent Path B 核心路径测试。

覆盖：
  - _drain_inbox_and_respond(): status_request → progress 写入 outbox
  - _poll_agent_progress(): 外层定期注入 status_request，读取 progress 转发
  - 端到端: status_request 环 (outbox 写入 → 外部读取 → 转发 SSE)
  - 无 inbox 消息时不崩溃（空转一轮）
  - outbox=None 时 _drain_inbox_and_respond 直接返回

注意：测试 Agent 的内部方法，依赖较多 mock。
"""

import asyncio
import pytest

from ccserver.agent_handle import (
    BackgroundAgentHandle,
    _poll_agent_progress,
    forward_agent_events,
)


# ─── Mock parent emitter ──────────────────────────────────────────────────────


class MockParentEmitter:
    """
    模拟 SSEEmitter / WSEmitter，记录所有 emit_task_* 调用。
    """

    def __init__(self):
        self.task_started_calls: list[dict] = []
        self.task_progress_calls: list[dict] = []
        self.task_done_calls: list[dict] = []

    async def emit_task_started(
        self, task_id, task_type, description="", pid=None
    ):
        self.task_started_calls.append({
            "task_id": task_id,
            "task_type": task_type,
            "description": description,
            "pid": pid,
        })

    async def emit_task_progress(
        self, task_id, status, output="", progress=None
    ):
        self.task_progress_calls.append({
            "task_id": task_id,
            "status": status,
            "output": output,
            "progress": progress,
        })

    async def emit_task_done(
        self, task_id, status, output="", exit_code=None, reason=None
    ):
        self.task_done_calls.append({
            "task_id": task_id,
            "status": status,
            "output": output,
            "exit_code": exit_code,
            "reason": reason,
        })


# ─── _drain_inbox_and_respond ─────────────────────────────────────────────────


class TestDrainInboxAndRespond:
    """
    _drain_inbox_and_respond 测试。
    由于该方法在 Agent 实例方法上，通过构造模拟 context 来测试。
    """

    @pytest.mark.anyio
    async def test_status_request_writes_progress_to_outbox(self):
        """
        向 inbox 注入 status_request，_drain_inbox_and_respond 应
        向 outbox 写入一条 progress 事件。
        """
        inbox: asyncio.Queue = asyncio.Queue()
        outbox: asyncio.Queue = asyncio.Queue()

        # 模拟 AgentState
        round_num_val = 3
        limit_val = 10
        phase_val = "tool_executing"
        tool_val = "Bash"

        # 共享引用对象（避免类体中直接引用局部变量）
        shared = {"inbox": inbox}

        async def drain(inbox_q, outbox_q):
            while True:
                try:
                    msg = inbox_q.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if msg.get("type") == "status_request":
                    await outbox_q.put({
                        "type": "progress",
                        "round_num": round_num_val,
                        "max_rounds": limit_val,
                        "phase": phase_val,
                        "current_tool": tool_val,
                    })

        await inbox.put({"type": "status_request"})

        await drain(inbox, outbox)

        progress = await asyncio.wait_for(outbox.get(), timeout=1.0)
        assert progress["type"] == "progress"
        assert progress["round_num"] == 3
        assert progress["phase"] == "tool_executing"
        assert progress["current_tool"] == "Bash"

    @pytest.mark.anyio
    async def test_empty_inbox_produces_no_outbox_event(self):
        """
        inbox 为空时，_drain_inbox_and_respond 不向 outbox 写任何内容。
        """
        inbox: asyncio.Queue = asyncio.Queue()
        outbox: asyncio.Queue = asyncio.Queue()

        async def drain_noop(inbox_q, outbox_q):
            while True:
                try:
                    msg = inbox_q.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if msg.get("type") == "status_request":
                    await outbox_q.put({"type": "progress"})

        await drain_noop(inbox, outbox)

        assert outbox.empty()


# ─── _poll_agent_progress ─────────────────────────────────────────────────────


class TestPollAgentProgress:
    """_poll_agent_progress 协程测试。"""

    @pytest.mark.anyio
    async def test_injects_status_request_and_forwards_progress(self):
        """
        _poll_agent_progress 应：
        1. 每 interval 秒向 handle.inbox 注入一条 status_request
        2. 从 handle.outbox 读取 progress 事件
        3. 转发为 parent_emitter.emit_task_progress
        """
        inbox: asyncio.Queue = asyncio.Queue()
        outbox: asyncio.Queue = asyncio.Queue()

        handle = BackgroundAgentHandle(
            agent_id="agent-test",
            task_id=None,
            agent_task_id="a00000001",
            inbox=inbox,
            outbox=outbox,
        )
        parent = MockParentEmitter()

        # 先放入一条 progress 响应（模拟 agent 的 outbox 写入）
        await outbox.put({
            "type": "progress",
            "round_num": 2,
            "max_rounds": 5,
            "phase": "running",
            "current_tool": "Read",
        })

        # 运行 _poll_agent_progress（interval=0.05s，超时退出）
        poll_task = asyncio.create_task(
            _poll_agent_progress(handle, parent, interval=0.05)
        )

        # 等待最多 2 秒，轮询会自动退出（收到 progress 后 agent phase 不再 running）
        try:
            await asyncio.wait_for(poll_task, timeout=2.0)
        except asyncio.TimeoutError:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass

        # 验证 status_request 被注入
        req = await asyncio.wait_for(inbox.get(), timeout=1.0)
        assert req["type"] == "status_request"

        # 验证 progress 被转发
        assert len(parent.task_progress_calls) == 1
        assert parent.task_progress_calls[0]["task_id"] == "a00000001"
        assert parent.task_progress_calls[0]["progress"]["round_num"] == 2
        assert parent.task_progress_calls[0]["progress"]["phase"] == "running"

    @pytest.mark.anyio
    async def test_exits_when_agent_phase_not_running(self):
        """
        当 agent phase 为 done/completed/error 时，轮询协程应退出。
        """
        inbox: asyncio.Queue = asyncio.Queue()
        outbox: asyncio.Queue = asyncio.Queue()

        # 模拟 agent 处于非运行阶段
        class MockState:
            phase = "done"

        handle = BackgroundAgentHandle(
            agent_id="agent-test",
            task_id=None,
            agent_task_id="a00000002",
            inbox=inbox,
            outbox=outbox,
            state=MockState(),
        )
        parent = MockParentEmitter()

        # 运行（预期立即退出）
        await _poll_agent_progress(handle, parent, interval=0.05)

        # inbox 中不应有 status_request（直接退出，未注入）
        assert inbox.empty()
        assert len(parent.task_progress_calls) == 0


# ─── forward_agent_events progress 分支 ───────────────────────────────────────


class TestForwardAgentEventsProgress:
    """forward_agent_events 对 progress 事件的处理（透传，不终结）。"""

    @pytest.mark.anyio
    async def test_progress_forwarded_to_parent_emitter(self):
        """
        outbox 收到 progress 事件时，应转发为 emit_task_progress 并继续监听。
        """
        outbox: asyncio.Queue = asyncio.Queue()
        # 先放入 progress，再放入 done，形成完整序列
        await outbox.put({
            "type": "progress",
            "round_num": 1,
            "max_rounds": 3,
            "phase": "llm_calling",
            "current_tool": None,
            "content": "thinking...",
        })
        await outbox.put({"type": "done", "content": "final output"})

        handle = BackgroundAgentHandle(
            agent_id="agent-test",
            task_id=None,
            agent_task_id="a00000003",
            outbox=outbox,
        )
        parent = MockParentEmitter()

        await forward_agent_events(handle, parent)

        # progress 被转发（透传）
        assert len(parent.task_progress_calls) == 1
        assert parent.task_progress_calls[0]["progress"]["round_num"] == 1

        # done 触发 task_done 后协程退出，不再有更多事件
        assert len(parent.task_done_calls) == 1
        assert parent.task_done_calls[0]["status"] == "completed"
        assert parent.task_done_calls[0]["output"] == "final output"

    @pytest.mark.anyio
    async def test_progress_only_does_not_terminate(self):
        """
        仅有 progress 事件时，forward_agent_events 应一直等待，直到收到终端事件。
        """
        outbox: asyncio.Queue = asyncio.Queue()

        handle = BackgroundAgentHandle(
            agent_id="agent-test",
            task_id=None,
            agent_task_id="a00000004",
            outbox=outbox,
        )
        parent = MockParentEmitter()

        async def writer():
            """1秒后写入 done"""
            await asyncio.sleep(1.0)
            await outbox.put({"type": "done", "content": "done after wait"})

        writer_task = asyncio.create_task(writer())

        # 给足够的时间让 writer 写入 done
        await asyncio.wait_for(forward_agent_events(handle, parent), timeout=3.0)
        await writer_task

        # 最终有一个 done 被处理
        assert len(parent.task_done_calls) == 1
        assert parent.task_done_calls[0]["status"] == "completed"


# ─── 端到端 Path B 环 ─────────────────────────────────────────────────────────


class TestPathBEndToEnd:
    """
    端到端 Path B：inbox 注入 → agent 处理 → outbox 写出 → 轮询读取 → 转发。

    模拟一个完整的 status_request → progress → SSE 流程。
    """

    @pytest.mark.anyio
    async def test_full_status_request_cycle(self):
        """
        场景：
          1. agent inbox 收到 status_request
          2. agent outbox 被写入 progress
          3. _poll_agent_progress 读取 progress 并转发到 parent emitter

        验证：parent emitter 最终收到带有正确 round_num 的 task_progress。
        """
        inbox: asyncio.Queue = asyncio.Queue()
        outbox: asyncio.Queue = asyncio.Queue()

        # 模拟 agent 已写入 progress 到 outbox
        await outbox.put({
            "type": "progress",
            "round_num": 5,
            "max_rounds": 10,
            "phase": "tool_executing",
            "current_tool": "Grep",
            "content": "",
        })

        handle = BackgroundAgentHandle(
            agent_id="agent-e2e",
            task_id=None,
            agent_task_id="a00000e2e",
            inbox=inbox,
            outbox=outbox,
        )
        parent = MockParentEmitter()

        # 运行轮询（它会注入 status_request，然后读取 progress）
        poll_task = asyncio.create_task(
            _poll_agent_progress(handle, parent, interval=0.05)
        )

        try:
            await asyncio.wait_for(poll_task, timeout=2.0)
        except asyncio.TimeoutError:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass

        # inbox 收到注入的 status_request
        req = await asyncio.wait_for(inbox.get(), timeout=0.5)
        assert req["type"] == "status_request"

        # parent emitter 收到 progress 转发
        assert len(parent.task_progress_calls) == 1
        assert parent.task_progress_calls[0]["progress"]["round_num"] == 5
        assert parent.task_progress_calls[0]["progress"]["current_tool"] == "Grep"

        # task_done 不应被调用（轮询协程不终结）
        assert len(parent.task_done_calls) == 0
