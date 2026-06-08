"""
tests/test_agent_task_progress.py — EventBus 推送模型的 PROGRESS 事件测试。

背景：
  旧的 Path B 轮询模型（status_request → outbox → _poll_agent_progress）
  已被 EventBus 推送模型取代：
    - _loop() 每轮工具调用前主动 publish EventType.PROGRESS 到 EventBus
    - _forward_bus_events() 闭包订阅 EventBus，转发给父级 emitter
    - 不再需要外部轮询注入 status_request，也不再需要 outbox

覆盖：
  - EventBus PROGRESS 事件能被订阅者收到
  - filter_fn 确保只有目标 agent 的事件被转发（隔离多 agent）
  - PROGRESS 事件 payload 格式正确（round_num, max_rounds, phase, current_tool）
  - DONE/ERROR/CANCELLED 终端事件正确触发退出
  - _forward_bus_events 等效逻辑：订阅 → 收 PROGRESS → 转发 → 收 DONE → 退出
"""

import asyncio
import pytest

from ccserver.event_bus import AgentEvent, EventBus, EventType


# ─── Mock parent emitter ──────────────────────────────────────────────────────


class MockParentEmitter:
    """
    模拟 SSEEmitter / WSEmitter，记录所有 emit_task_* 调用。
    """

    def __init__(self):
        self.task_progress_calls: list[dict] = []
        self.task_done_calls: list[dict] = []

    async def emit_task_progress(self, task_id, status, output="", progress=None):
        """记录 progress 调用。"""
        self.task_progress_calls.append({
            "task_id": task_id,
            "status": status,
            "output": output,
            "progress": progress,
        })

    async def emit_task_done(self, task_id, status, output="", exit_code=None, reason=None):
        """记录 done 调用。"""
        self.task_done_calls.append({
            "task_id": task_id,
            "status": status,
            "output": output,
            "exit_code": exit_code,
            "reason": reason,
        })


# ─── EventBus PROGRESS 事件推送 ────────────────────────────────────────────────


class TestEventBusProgressPush:
    """PROGRESS 事件通过 EventBus 推送，订阅者正确接收。"""

    @pytest.mark.asyncio
    async def test_progress_event_received_by_subscriber(self):
        """
        publish 一条 PROGRESS 事件后，订阅者能收到，且 payload 格式正确。
        """
        bus = EventBus()
        agent_id = "agent-prog-01"

        # 订阅指定 agent 的事件
        def filter_fn(e):
            return e.agent_id == agent_id
        async with bus.subscribe("test_sub", filter_fn=filter_fn) as sub:
            await bus.publish(AgentEvent(
                type=EventType.PROGRESS,
                agent_id=agent_id,
                session_id="session-1",
                payload={
                    "progress": {
                        "round_num": 3,
                        "max_rounds": 10,
                        "phase": "tool_executing",
                        "current_tool": "Bash",
                    }
                },
            ))
            event = await sub.get(timeout=1.0)

        assert event is not None
        assert event.type == EventType.PROGRESS
        assert event.payload["progress"]["round_num"] == 3
        assert event.payload["progress"]["phase"] == "tool_executing"
        assert event.payload["progress"]["current_tool"] == "Bash"

    @pytest.mark.asyncio
    async def test_progress_from_other_agent_not_received(self):
        """
        filter_fn 只匹配指定 agent_id，其他 agent 的 PROGRESS 不会被收到。
        """
        bus = EventBus()
        my_agent_id = "agent-me"
        other_agent_id = "agent-other"

        filter_fn = lambda e: e.agent_id == my_agent_id  # noqa: E731
        async with bus.subscribe("test_sub", filter_fn=filter_fn) as sub:
            await bus.publish(AgentEvent(
                type=EventType.PROGRESS,
                agent_id=other_agent_id,
                session_id="session-1",
                payload={"progress": {"round_num": 1, "max_rounds": 5,
                                       "phase": "running", "current_tool": None}},
            ))
            # 短超时，不应收到事件
            event = await sub.get(timeout=0.1)

        assert event is None


# ─── _forward_bus_events 等效逻辑 ──────────────────────────────────────────────


class TestForwardBusEventsLogic:
    """
    模拟 spawn_background() 中的 _forward_bus_events 闭包行为：
      订阅 EventBus → 收 PROGRESS 转发 → 收 DONE/ERROR/CANCELLED 后退出
    """

    async def _run_forwarder(
        self,
        bus: EventBus,
        agent_task_id: str,
        child_agent_id: str,
        parent_emitter: MockParentEmitter,
        agent_task_state=None,
    ):
        """
        模拟 _forward_bus_events 的核心逻辑，方便在测试中复用。

        Args:
            bus:              Session EventBus。
            agent_task_id:    后台任务 ID，用于 emit_task_* 的 task_id 参数。
            child_agent_id:   子 Agent ID，只处理来自该 Agent 的事件。
            parent_emitter:   父级 emitter，接收转发的事件。
            agent_task_state: 可选的 AgentTaskState，用于状态更新。
        """
        sub_id = f"forward_{agent_task_id}"
        def filter_fn(e):
            return e.agent_id == child_agent_id

        async with bus.subscribe(sub_id, filter_fn=filter_fn) as sub:
            while True:
                event = await sub.get(timeout=5.0)
                if event is None:
                    break  # 超时退出（测试不应触发此分支）

                etype = event.type

                if etype == EventType.PROGRESS:
                    # 转发进度事件，不退出循环
                    progress_info = event.payload.get("progress") or {}
                    await parent_emitter.emit_task_progress(
                        task_id=agent_task_id,
                        status="running",
                        output="",
                        progress=progress_info,
                    )

                elif etype == EventType.DONE:
                    content = event.payload.get("content", "")
                    if agent_task_state is not None:
                        agent_task_state.mark_completed(result=content)
                    await parent_emitter.emit_task_done(
                        task_id=agent_task_id,
                        status="completed",
                        output=content[:50_000] if content else "",
                        exit_code=None,
                        reason=None,
                    )
                    break  # 终端事件，退出

                elif etype == EventType.ERROR:
                    error_msg = event.payload.get("error", "unknown error")
                    if agent_task_state is not None:
                        agent_task_state.mark_failed(error=error_msg)
                    await parent_emitter.emit_task_done(
                        task_id=agent_task_id,
                        status="failed",
                        output="",
                        exit_code=None,
                        reason=error_msg[:500],
                    )
                    break  # 终端事件，退出

                elif etype == EventType.CANCELLED:
                    if agent_task_state is not None:
                        agent_task_state.mark_cancelled()
                    await parent_emitter.emit_task_done(
                        task_id=agent_task_id,
                        status="cancelled",
                        output="",
                        exit_code=None,
                        reason="cancelled",
                    )
                    break  # 终端事件，退出

    @pytest.mark.asyncio
    async def test_progress_forwarded_then_done_exits(self):
        """
        收到 PROGRESS 后继续，收到 DONE 后转发 task_done 并退出。
        """
        bus = EventBus()
        agent_id = "child-agent-01"
        agent_task_id = "a00000p01"
        parent = MockParentEmitter()

        # 启动转发协程（等待事件）
        forwarder = asyncio.create_task(
            self._run_forwarder(bus, agent_task_id, agent_id, parent)
        )
        # 让出控制权，确保 forwarder 执行到 subscribe 后再 publish
        await asyncio.sleep(0.05)

        # 模拟 _loop() 推送 PROGRESS
        await bus.publish(AgentEvent(
            type=EventType.PROGRESS,
            agent_id=agent_id,
            session_id="s1",
            payload={"progress": {"round_num": 2, "max_rounds": 5,
                                   "phase": "tool_executing", "current_tool": "Read"}},
        ))
        # 等一下让事件被处理
        await asyncio.sleep(0.05)

        # 模拟 _loop() 完成，推送 DONE
        await bus.publish(AgentEvent(
            type=EventType.DONE,
            agent_id=agent_id,
            session_id="s1",
            payload={"content": "task finished"},
        ))

        await asyncio.wait_for(forwarder, timeout=2.0)

        # PROGRESS 被转发
        assert len(parent.task_progress_calls) == 1
        assert parent.task_progress_calls[0]["task_id"] == agent_task_id
        assert parent.task_progress_calls[0]["progress"]["round_num"] == 2

        # DONE 触发 task_done
        assert len(parent.task_done_calls) == 1
        assert parent.task_done_calls[0]["status"] == "completed"
        assert parent.task_done_calls[0]["output"] == "task finished"

    @pytest.mark.asyncio
    async def test_error_event_exits_with_failed(self):
        """
        收到 ERROR 事件时，应转发 task_done(failed) 并退出。
        """
        bus = EventBus()
        agent_id = "child-agent-02"
        agent_task_id = "a00000p02"
        parent = MockParentEmitter()

        forwarder = asyncio.create_task(
            self._run_forwarder(bus, agent_task_id, agent_id, parent)
        )
        await asyncio.sleep(0.05)

        await bus.publish(AgentEvent(
            type=EventType.ERROR,
            agent_id=agent_id,
            session_id="s1",
            payload={"error": "LLM timeout after retries"},
        ))

        await asyncio.wait_for(forwarder, timeout=2.0)

        assert len(parent.task_done_calls) == 1
        assert parent.task_done_calls[0]["status"] == "failed"
        assert "LLM timeout" in parent.task_done_calls[0]["reason"]

    @pytest.mark.asyncio
    async def test_cancelled_event_exits_with_cancelled(self):
        """
        收到 CANCELLED 事件时，应转发 task_done(cancelled) 并退出。
        """
        bus = EventBus()
        agent_id = "child-agent-03"
        agent_task_id = "a00000p03"
        parent = MockParentEmitter()

        forwarder = asyncio.create_task(
            self._run_forwarder(bus, agent_task_id, agent_id, parent)
        )
        await asyncio.sleep(0.05)

        await bus.publish(AgentEvent(
            type=EventType.CANCELLED,
            agent_id=agent_id,
            session_id="s1",
        ))

        await asyncio.wait_for(forwarder, timeout=2.0)

        assert len(parent.task_done_calls) == 1
        assert parent.task_done_calls[0]["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_multiple_progress_before_done(self):
        """
        多轮 PROGRESS 后 DONE，每轮 PROGRESS 都被转发。
        """
        bus = EventBus()
        agent_id = "child-agent-04"
        agent_task_id = "a00000p04"
        parent = MockParentEmitter()

        forwarder = asyncio.create_task(
            self._run_forwarder(bus, agent_task_id, agent_id, parent)
        )
        await asyncio.sleep(0.05)

        # 模拟 3 轮工具调用
        for i in range(1, 4):
            await bus.publish(AgentEvent(
                type=EventType.PROGRESS,
                agent_id=agent_id,
                session_id="s1",
                payload={"progress": {"round_num": i, "max_rounds": 3,
                                       "phase": "tool_executing", "current_tool": "Bash"}},
            ))
            await asyncio.sleep(0.02)  # 让事件被处理

        await bus.publish(AgentEvent(
            type=EventType.DONE,
            agent_id=agent_id,
            session_id="s1",
            payload={"content": "all rounds done"},
        ))

        await asyncio.wait_for(forwarder, timeout=2.0)

        # 3 条 PROGRESS + 1 条 DONE
        assert len(parent.task_progress_calls) == 3
        assert parent.task_progress_calls[0]["progress"]["round_num"] == 1
        assert parent.task_progress_calls[2]["progress"]["round_num"] == 3
        assert len(parent.task_done_calls) == 1
        assert parent.task_done_calls[0]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_agent_task_state_updated_on_done(self):
        """
        收到 DONE 时 agent_task_state.mark_completed 应被调用。
        """
        from ccserver.tasks import AgentTaskState, AgentTaskStatus

        bus = EventBus()
        agent_id = "child-agent-05"
        agent_task_id = "a00000p05"
        parent = MockParentEmitter()

        task_state = AgentTaskState(id=agent_task_id, agent_id=agent_id)
        task_state.mark_running()

        forwarder = asyncio.create_task(
            self._run_forwarder(bus, agent_task_id, agent_id, parent,
                                agent_task_state=task_state)
        )
        await asyncio.sleep(0.05)

        await bus.publish(AgentEvent(
            type=EventType.DONE,
            agent_id=agent_id,
            session_id="s1",
            payload={"content": "result text"},
        ))

        await asyncio.wait_for(forwarder, timeout=2.0)

        # AgentTaskState 应更新为 COMPLETED
        assert task_state.status == AgentTaskStatus.COMPLETED
        assert task_state.result == "result text"
