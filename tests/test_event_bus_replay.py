"""
tests/test_event_bus_replay.py — EventBus 环形重放缓冲测试（P2-4）。

覆盖：
  - replay_buffer_size=N 时缓冲正常写入
  - replay_buffer_size=0 时禁用重放
  - visibility="hidden" 事件不写入缓冲
  - replay_since(None) 返回全量
  - replay_since(last_event_id) 返回之后的事件
  - last_event_id 不在缓冲中时返回全量（缓冲已轮转场景）
  - 缓冲区满时自动淘汰最旧事件（deque maxlen）
  - filter_fn 过滤
"""

import pytest
from pathlib import Path

from ccserver.event_bus import EventBus, AgentEvent, EventType


def make_event(type_: str = "token", visibility: str = "full", payload: dict = None) -> AgentEvent:
    """辅助：创建测试用 AgentEvent。"""
    return AgentEvent(
        type=type_,
        agent_id="a1",
        session_id="s1",
        payload=payload or {"token": "x"},
        visibility=visibility,
    )


# ─── 基础功能 ─────────────────────────────────────────────────────────────────

class TestEventBusReplayBuffer:

    @pytest.mark.anyio
    async def test_replay_disabled_when_size_zero(self):
        """replay_buffer_size=0 时禁用重放，replay_since 返回空列表。"""
        bus = EventBus(replay_buffer_size=0)
        assert bus._replay_enabled is False
        e = make_event()
        await bus.publish(e)
        assert bus.replay_since() == []

    @pytest.mark.anyio
    async def test_replay_enabled_when_size_positive(self):
        """replay_buffer_size>0 时启用重放。"""
        bus = EventBus(replay_buffer_size=10)
        assert bus._replay_enabled is True

    @pytest.mark.anyio
    async def test_published_event_in_buffer(self):
        """publish 后事件写入缓冲区，replay_since 可取回。"""
        bus = EventBus(replay_buffer_size=10)
        e = make_event("token")
        await bus.publish(e)
        replayed = bus.replay_since()
        assert len(replayed) == 1
        assert replayed[0].event_id == e.event_id

    @pytest.mark.anyio
    async def test_hidden_events_not_in_buffer(self):
        """visibility=hidden 的事件（进程内通知）不写入重放缓冲。"""
        bus = EventBus(replay_buffer_size=10)
        hidden = make_event("mailbox_arrived", visibility="hidden")
        visible = make_event("token", visibility="full")
        await bus.publish(hidden)
        await bus.publish(visible)
        replayed = bus.replay_since()
        assert len(replayed) == 1
        assert replayed[0].visibility == "full"

    @pytest.mark.anyio
    async def test_replay_since_none_returns_all(self):
        """replay_since(None) 返回缓冲区全量事件。"""
        bus = EventBus(replay_buffer_size=10)
        events = [make_event() for _ in range(3)]
        for e in events:
            await bus.publish(e)
        replayed = bus.replay_since(last_event_id=None)
        assert len(replayed) == 3

    @pytest.mark.anyio
    async def test_replay_since_event_id_returns_subsequent(self):
        """replay_since(last_event_id=e1.event_id) 返回 e1 之后的事件。"""
        bus = EventBus(replay_buffer_size=10)
        e1 = make_event("token")
        e2 = make_event("tool_start")
        e3 = make_event("done")
        await bus.publish(e1)
        await bus.publish(e2)
        await bus.publish(e3)

        replayed = bus.replay_since(last_event_id=e1.event_id)
        assert len(replayed) == 2
        assert replayed[0].event_id == e2.event_id
        assert replayed[1].event_id == e3.event_id

    @pytest.mark.anyio
    async def test_replay_since_last_event_returns_empty(self):
        """replay_since(最后一个事件的 id) 返回空列表。"""
        bus = EventBus(replay_buffer_size=10)
        e1 = make_event()
        await bus.publish(e1)
        replayed = bus.replay_since(last_event_id=e1.event_id)
        assert replayed == []

    @pytest.mark.anyio
    async def test_replay_since_unknown_id_returns_all(self):
        """last_event_id 不在缓冲区（缓冲已轮转）时，返回全量缓冲事件。"""
        bus = EventBus(replay_buffer_size=10)
        events = [make_event() for _ in range(3)]
        for e in events:
            await bus.publish(e)
        replayed = bus.replay_since(last_event_id="nonexistent-id")
        # 未找到时回退为全量
        assert len(replayed) == 3

    @pytest.mark.anyio
    async def test_buffer_maxlen_evicts_oldest(self):
        """缓冲区满时自动淘汰最旧事件（deque maxlen 行为）。"""
        bus = EventBus(replay_buffer_size=3)  # 只保留最新 3 个
        events = [make_event() for _ in range(5)]
        for e in events:
            await bus.publish(e)
        replayed = bus.replay_since()
        # 只有最新 3 个在缓冲区
        assert len(replayed) == 3
        # 最新 3 个的 event_id 与 events[-3:] 一致
        replayed_ids = {e.event_id for e in replayed}
        expected_ids = {e.event_id for e in events[-3:]}
        assert replayed_ids == expected_ids

    @pytest.mark.anyio
    async def test_replay_filter_fn(self):
        """filter_fn 应只返回匹配的事件。"""
        bus = EventBus(replay_buffer_size=10)
        token_event = make_event("token")
        done_event = make_event("done")
        await bus.publish(token_event)
        await bus.publish(done_event)

        replayed = bus.replay_since(
            filter_fn=lambda e: e.type == EventType.DONE
        )
        assert len(replayed) == 1
        assert replayed[0].type == EventType.DONE

    @pytest.mark.anyio
    async def test_replay_with_overflow_dir(self, tmp_path: Path):
        """配置 overflow_dir 时，重放缓冲仍正常工作（与背压系统不干扰）。"""
        bus = EventBus(overflow_dir=tmp_path / "overflow", replay_buffer_size=5)
        events = [make_event() for _ in range(3)]
        for e in events:
            await bus.publish(e)
        replayed = bus.replay_since()
        assert len(replayed) == 3

    @pytest.mark.anyio
    async def test_default_replay_buffer_size_is_500(self):
        """默认 replay_buffer_size=500，缓冲区 maxlen 为 500。"""
        bus = EventBus()
        assert bus._replay_enabled is True
        assert bus._replay_buffer.maxlen == 500
