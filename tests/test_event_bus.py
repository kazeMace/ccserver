"""
test_event_bus — EventBus、AgentEvent、BusEmitter 的单元测试。

测试覆盖：
  - AgentEvent 创建和字段默认值
  - EventBus 单订阅者收到事件
  - EventBus 多订阅者各自独立收到事件（fan-out）
  - EventBus filter_fn 过滤（匹配 / 不匹配）
  - EventBus 背压：Queue 满时丢弃最旧事件
  - Subscription 上下文管理器退出后自动注销
  - BusEmitter 各个 emit_* 方法产出正确的 AgentEvent
"""

import asyncio
import pytest

from ccserver.event_bus import AgentEvent, EventBus, EventType
from ccserver.emitters.bus_emitter import BusEmitter


# ── 辅助工具 ──────────────────────────────────────────────────────────────────

def make_event(event_type: str = EventType.TOKEN, agent_id: str = "agent-a", to_agent: str | None = None) -> AgentEvent:
    """创建一个测试用 AgentEvent。"""
    return AgentEvent(
        type=event_type,
        agent_id=agent_id,
        session_id="session-1",
        payload={"token": "hello"},
        to_agent=to_agent,
    )


# ── AgentEvent 测试 ───────────────────────────────────────────────────────────

class TestAgentEvent:

    def test_default_fields_are_set(self):
        """event_id 和 ts 应该自动生成。"""
        event = AgentEvent(type="token", agent_id="a", session_id="s")
        assert event.event_id != ""
        assert event.ts > 0

    def test_two_events_have_different_ids(self):
        """每个事件的 event_id 应该唯一。"""
        e1 = AgentEvent(type="token", agent_id="a", session_id="s")
        e2 = AgentEvent(type="token", agent_id="a", session_id="s")
        assert e1.event_id != e2.event_id

    def test_payload_defaults_to_empty_dict(self):
        """不传 payload 时默认为空字典。"""
        event = AgentEvent(type="done", agent_id="a", session_id="s")
        assert event.payload == {}

    def test_to_agent_defaults_to_none(self):
        """不传 to_agent 时默认为 None（广播）。"""
        event = AgentEvent(type="done", agent_id="a", session_id="s")
        assert event.to_agent is None


# ── EventBus 测试 ─────────────────────────────────────────────────────────────

class TestEventBus:

    @pytest.mark.asyncio
    async def test_single_subscriber_receives_event(self):
        """单个订阅者能收到 publish 的事件。"""
        bus = EventBus()
        async with bus.subscribe("sub1") as sub:
            event = make_event()
            await bus.publish(event)
            received = await sub.get(timeout=1.0)
        assert received is not None
        assert received.type == EventType.TOKEN

    @pytest.mark.asyncio
    async def test_multiple_subscribers_each_receive_event(self):
        """多个订阅者各自独立收到同一事件，互不影响（fan-out）。"""
        bus = EventBus()
        async with bus.subscribe("sub1") as sub1:
            async with bus.subscribe("sub2") as sub2:
                event = make_event()
                await bus.publish(event)
                r1 = await sub1.get(timeout=1.0)
                r2 = await sub2.get(timeout=1.0)
        assert r1 is not None
        assert r2 is not None
        # 两个订阅者收到的是独立的同一事件对象
        assert r1.event_id == r2.event_id

    @pytest.mark.asyncio
    async def test_filter_fn_passes_matching_event(self):
        """filter_fn 匹配时，订阅者能收到事件。"""
        bus = EventBus()
        filter_fn = lambda e: e.type == EventType.DONE
        async with bus.subscribe("sub1", filter_fn=filter_fn) as sub:
            await bus.publish(make_event(EventType.DONE))
            received = await sub.get(timeout=1.0)
        assert received is not None
        assert received.type == EventType.DONE

    @pytest.mark.asyncio
    async def test_filter_fn_blocks_non_matching_event(self):
        """filter_fn 不匹配时，订阅者收不到事件。"""
        bus = EventBus()
        filter_fn = lambda e: e.type == EventType.DONE
        async with bus.subscribe("sub1", filter_fn=filter_fn) as sub:
            # publish 一个 TOKEN 事件，filter 应该过滤掉
            await bus.publish(make_event(EventType.TOKEN))
            # 短暂等待，确认没有事件
            received = await sub.get(timeout=0.1)
        assert received is None

    @pytest.mark.asyncio
    async def test_to_agent_filter(self):
        """用 to_agent 实现点对点投递：只有目标订阅者收到。"""
        bus = EventBus()
        filter_a = lambda e: e.to_agent == "agent-a" or e.to_agent is None
        filter_b = lambda e: e.to_agent == "agent-b" or e.to_agent is None

        async with bus.subscribe("sub_a", filter_fn=filter_a) as sub_a:
            async with bus.subscribe("sub_b", filter_fn=filter_b) as sub_b:
                # 发给 agent-a 的定向消息
                await bus.publish(make_event(to_agent="agent-a"))
                r_a = await sub_a.get(timeout=0.5)
                r_b = await sub_b.get(timeout=0.1)  # agent-b 收不到

        assert r_a is not None       # agent-a 收到
        assert r_b is None           # agent-b 收不到

    @pytest.mark.asyncio
    async def test_subscription_context_manager_unsubscribes_on_exit(self):
        """退出 async with 后，订阅者自动注销，不再出现在订阅者列表中。"""
        bus = EventBus()
        async with bus.subscribe("sub1") as sub:
            assert bus.subscriber_count() == 1
        # 退出后应该自动注销
        assert bus.subscriber_count() == 0

    @pytest.mark.asyncio
    async def test_backpressure_drops_oldest_when_queue_full(self):
        """Queue 满时丢弃最旧事件，新事件能放入，不阻塞 publish。"""
        bus = EventBus()
        # 用 maxsize=2 的小队列测试背压
        async with bus.subscribe("sub1", maxsize=2) as sub:
            # 放入 3 个事件，第 1 个会被丢弃
            e1 = make_event(EventType.TOKEN)
            e1_id = e1.event_id
            e2 = make_event(EventType.TOOL_START)
            e3 = make_event(EventType.DONE)

            await bus.publish(e1)
            await bus.publish(e2)
            # 此时队列已满（maxsize=2），再 publish 会触发背压
            await bus.publish(e3)

            r1 = await sub.get(timeout=0.5)
            r2 = await sub.get(timeout=0.5)

        # 最旧的 e1 被丢弃，收到的是 e2 和 e3
        assert r1 is not None
        assert r2 is not None
        assert r1.event_id != e1_id   # e1 已被丢弃
        assert r2.type == EventType.DONE

    @pytest.mark.asyncio
    async def test_no_subscribers_publish_does_nothing(self):
        """没有订阅者时，publish 正常执行，不抛异常。"""
        bus = EventBus()
        # 不应该抛任何异常
        await bus.publish(make_event())

    @pytest.mark.asyncio
    async def test_subscriber_count(self):
        """subscriber_count 返回正确的订阅者数量。"""
        bus = EventBus()
        assert bus.subscriber_count() == 0
        async with bus.subscribe("sub1") as _:
            async with bus.subscribe("sub2") as _:
                assert bus.subscriber_count() == 2
            assert bus.subscriber_count() == 1
        assert bus.subscriber_count() == 0


# ── BusEmitter 测试 ───────────────────────────────────────────────────────────

class TestBusEmitter:
    """测试 BusEmitter 的每个 emit_* 方法都能产出正确的 AgentEvent。"""

    @pytest.mark.asyncio
    async def test_emit_token(self):
        """emit_token 应该产出 EventType.TOKEN 事件，payload 含 token 字段。"""
        bus = EventBus()
        emitter = BusEmitter(bus, agent_id="a1", session_id="s1")
        async with bus.subscribe("test") as sub:
            await emitter.emit_token("hello world")
            event = await sub.get(timeout=1.0)
        assert event is not None
        assert event.type == EventType.TOKEN
        assert event.payload["token"] == "hello world"
        assert event.agent_id == "a1"
        assert event.session_id == "s1"

    @pytest.mark.asyncio
    async def test_emit_done(self):
        """emit_done 应该产出 EventType.DONE 事件，payload 含 content 字段。"""
        bus = EventBus()
        emitter = BusEmitter(bus, agent_id="a1", session_id="s1")
        async with bus.subscribe("test") as sub:
            await emitter.emit_done("final answer")
            event = await sub.get(timeout=1.0)
        assert event is not None
        assert event.type == EventType.DONE
        assert event.payload["content"] == "final answer"

    @pytest.mark.asyncio
    async def test_emit_error(self):
        """emit_error 应该产出 EventType.ERROR 事件，payload 含 error 字段。"""
        bus = EventBus()
        emitter = BusEmitter(bus, agent_id="a1", session_id="s1")
        async with bus.subscribe("test") as sub:
            await emitter.emit_error("something went wrong")
            event = await sub.get(timeout=1.0)
        assert event is not None
        assert event.type == EventType.ERROR
        assert event.payload["error"] == "something went wrong"

    @pytest.mark.asyncio
    async def test_emit_tool_start(self):
        """emit_tool_start 应该产出 EventType.TOOL_START 事件。"""
        bus = EventBus()
        emitter = BusEmitter(bus, agent_id="a1", session_id="s1")
        async with bus.subscribe("test") as sub:
            await emitter.emit_tool_start("Bash", "ls -la")
            event = await sub.get(timeout=1.0)
        assert event is not None
        assert event.type == EventType.TOOL_START
        assert event.payload["tool_name"] == "Bash"
        assert event.payload["preview"] == "ls -la"

    @pytest.mark.asyncio
    async def test_emit_tool_result(self):
        """emit_tool_result 应该产出 EventType.TOOL_DONE 事件。"""
        bus = EventBus()
        emitter = BusEmitter(bus, agent_id="a1", session_id="s1")
        async with bus.subscribe("test") as sub:
            await emitter.emit_tool_result("Bash", "file1.py\nfile2.py")
            event = await sub.get(timeout=1.0)
        assert event is not None
        assert event.type == EventType.TOOL_DONE
        assert event.payload["tool_name"] == "Bash"

    @pytest.mark.asyncio
    async def test_emit_task_progress(self):
        """emit_task_progress 应该产出 EventType.PROGRESS 事件。"""
        bus = EventBus()
        emitter = BusEmitter(bus, agent_id="a1", session_id="s1")
        async with bus.subscribe("test") as sub:
            await emitter.emit_task_progress(
                task_id="t1",
                status="running",
                progress={"round_num": 3, "max_rounds": 10},
            )
            event = await sub.get(timeout=1.0)
        assert event is not None
        assert event.type == EventType.PROGRESS
        assert event.payload["task_id"] == "t1"
        assert event.payload["progress"]["round_num"] == 3

    @pytest.mark.asyncio
    async def test_emit_raw_dict_via_emit(self):
        """直接调用 emit(dict) 时，type 字段正确传递。"""
        bus = EventBus()
        emitter = BusEmitter(bus, agent_id="a1", session_id="s1")
        async with bus.subscribe("test") as sub:
            await emitter.emit({"type": "compact", "reason": "context full"})
            event = await sub.get(timeout=1.0)
        assert event is not None
        assert event.type == "compact"
        assert event.payload["reason"] == "context full"

    @pytest.mark.asyncio
    async def test_emit_ask_user_returns_empty_string(self):
        """BusEmitter 不支持交互，emit_ask_user 应立即返回空字符串。"""
        bus = EventBus()
        emitter = BusEmitter(bus, agent_id="a1", session_id="s1")
        result = await emitter.emit_ask_user([{"question": "Continue?"}])
        assert result == ""

    @pytest.mark.asyncio
    async def test_emit_permission_request_returns_false(self):
        """BusEmitter 不支持交互，emit_permission_request 应立即返回 False。"""
        bus = EventBus()
        emitter = BusEmitter(bus, agent_id="a1", session_id="s1")
        result = await emitter.emit_permission_request("Bash", {"command": "rm -rf /"})
        assert result is False
