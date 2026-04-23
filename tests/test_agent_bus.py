"""
tests/test_agent_bus.py — SessionAgentBus 单元测试

覆盖：
  - register / unregister / get_mailbox
  - send 成功与失败
  - broadcast 与 exclude
  - list_agents
"""

import asyncio
import pytest

from ccserver.agent_bus import SessionAgentBus


# ─── 注册与邮箱 ────────────────────────────────────────────────────────────────


def test_register_returns_queue():
    bus = SessionAgentBus()
    q = bus.register("agent-1")
    assert isinstance(q, asyncio.Queue)
    assert bus.get_mailbox("agent-1") is q


def test_register_idempotent():
    bus = SessionAgentBus()
    q1 = bus.register("agent-1")
    q2 = bus.register("agent-1")
    assert q1 is q2


def test_unregister_removes_mailbox():
    bus = SessionAgentBus()
    bus.register("agent-1")
    bus.unregister("agent-1")
    assert bus.get_mailbox("agent-1") is None


def test_get_mailbox_nonexistent():
    bus = SessionAgentBus()
    assert bus.get_mailbox("not-found") is None


# ─── 发送消息 ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_success():
    bus = SessionAgentBus()
    bus.register("agent-1")
    ok = await bus.send("agent-1", {"type": "ping"})
    assert ok is True
    mailbox = bus.get_mailbox("agent-1")
    msg = await asyncio.wait_for(mailbox.get(), timeout=0.5)
    assert msg == {"type": "ping"}


@pytest.mark.asyncio
async def test_send_fail_not_registered():
    bus = SessionAgentBus()
    ok = await bus.send("agent-x", {"type": "ping"})
    assert ok is False


# ─── 广播 ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_broadcast_to_all():
    bus = SessionAgentBus()
    bus.register("a1")
    bus.register("a2")
    await bus.broadcast({"type": "alert"})

    m1 = bus.get_mailbox("a1")
    m2 = bus.get_mailbox("a2")
    assert (await asyncio.wait_for(m1.get(), timeout=0.5)) == {"type": "alert"}
    assert (await asyncio.wait_for(m2.get(), timeout=0.5)) == {"type": "alert"}


@pytest.mark.asyncio
async def test_broadcast_exclude_sender():
    bus = SessionAgentBus()
    bus.register("sender")
    bus.register("receiver")
    await bus.broadcast({"type": "alert"}, exclude="sender")

    sender_mbox = bus.get_mailbox("sender")
    receiver_mbox = bus.get_mailbox("receiver")

    # sender 不应收到消息
    assert sender_mbox.empty() is True
    msg = await asyncio.wait_for(receiver_mbox.get(), timeout=0.5)
    assert msg == {"type": "alert"}


# ─── 列表 ──────────────────────────────────────────────────────────────────────


def test_list_agents():
    bus = SessionAgentBus()
    bus.register("x")
    bus.register("y")
    agents = bus.list_agents()
    assert sorted(agents) == ["x", "y"]


def test_list_agents_empty():
    bus = SessionAgentBus()
    assert bus.list_agents() == []
