"""
tests/test_session_event_bus.py — Session 与 EventBus 集成测试。

覆盖：
  - Session 创建时自动初始化 EventBus
  - Session.event_bus 支持 publish / subscribe 基本操作
  - SessionManager.create() 创建的 Session 也具备 EventBus
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from ccserver.session import Session, SessionManager
from ccserver.event_bus import EventBus, AgentEvent, EventType
from ccserver.storage import FileStorageAdapter


# ─── 辅助工具 ──────────────────────────────────────────────────────────────────


def _make_storage(tmp_path: Path) -> FileStorageAdapter:
    """创建一个基于临时目录的 FileStorageAdapter。"""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    return FileStorageAdapter(sessions_dir)


# ─── Session 直接创建 ──────────────────────────────────────────────────────────


def test_session_initializes_event_bus(tmp_path):
    """Session 创建后 event_bus 应自动初始化且为 EventBus 类型。"""
    storage = _make_storage(tmp_path)
    session = Session(
        id="test-session-1",
        workdir=tmp_path / "workdir",
        project_root=tmp_path,
        storage=storage,
    )
    assert session.event_bus is not None
    assert isinstance(session.event_bus, EventBus)


@pytest.mark.asyncio
async def test_session_event_bus_publish_and_subscribe(tmp_path):
    """Session 的 event_bus 应支持基本的 publish 和 subscribe 操作。"""
    storage = _make_storage(tmp_path)
    session = Session(
        id="test-session-2",
        workdir=tmp_path / "workdir",
        project_root=tmp_path,
        storage=storage,
    )

    event = AgentEvent(
        type=EventType.TOKEN,
        agent_id="agent-a",
        session_id=session.id,
        payload={"token": "hello"},
    )

    # 订阅并接收事件
    async with session.event_bus.subscribe("test_sub") as sub:
        await session.event_bus.publish(event)
        received = await asyncio.wait_for(sub.get(), timeout=1.0)

    assert received is not None
    assert received.type == EventType.TOKEN
    assert received.payload["token"] == "hello"
    assert received.agent_id == "agent-a"


# ─── SessionManager 创建 ───────────────────────────────────────────────────────


def test_session_manager_create_initializes_event_bus(tmp_path):
    """通过 SessionManager.create() 创建的 Session 也应具备 EventBus。"""
    storage = _make_storage(tmp_path)
    sm = SessionManager(
        base_dir=tmp_path / "sessions",
        project_root=tmp_path,
        storage=storage,
    )
    session = sm.create(session_id="test-session-3")

    assert session.event_bus is not None
    assert isinstance(session.event_bus, EventBus)


@pytest.mark.asyncio
async def test_session_manager_event_bus_fan_out(tmp_path):
    """SessionManager 创建的 Session，其 EventBus 应支持多订阅者 fan-out。"""
    storage = _make_storage(tmp_path)
    sm = SessionManager(
        base_dir=tmp_path / "sessions",
        project_root=tmp_path,
        storage=storage,
    )
    session = sm.create(session_id="test-session-4")

    event = AgentEvent(
        type=EventType.DONE,
        agent_id="agent-b",
        session_id=session.id,
        payload={"content": "task completed"},
    )

    async with session.event_bus.subscribe("sub1") as sub1:
        async with session.event_bus.subscribe("sub2") as sub2:
            await session.event_bus.publish(event)
            r1 = await asyncio.wait_for(sub1.get(), timeout=1.0)
            r2 = await asyncio.wait_for(sub2.get(), timeout=1.0)

    assert r1 is not None
    assert r2 is not None
    assert r1.event_id == r2.event_id
    assert r1.type == EventType.DONE
    assert r1.payload["content"] == "task completed"
