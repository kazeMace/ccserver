"""
tests/test_background_agent.py — 后台 Agent 框架单元测试

覆盖：
  - BackgroundAgentHandle 创建与取消
  - Agent.spawn_background() 返回 handle
  - AgentScheduler spawn/get/cancel/list
  - 环境变量继承
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from ccserver.agent import Agent, AgentContext, AgentState
from ccserver.agent_handle import BackgroundAgentHandle
from ccserver.agent_scheduler import AgentScheduler
from ccserver.emitters.queue import QueueEmitter


def _make_session():
    session = MagicMock()
    session.settings = MagicMock()
    session.settings.denied_tools = frozenset()
    session.settings.allowed_tools = None
    session.settings.run_mode = "auto"
    session.mcp.schemas.return_value = []
    session.skills = MagicMock()
    session.project_root = Path("/tmp/test")
    session.messages = []
    return session


def _make_agent(tools=None, env_vars=None) -> Agent:
    session = _make_session()
    context = AgentContext(name="test-agent", messages=[], depth=0, env_vars=env_vars or {})
    return Agent(
        session=session,
        adapter=MagicMock(),
        emitter=MagicMock(),
        tools=tools or {"Read": MagicMock(name="Read")},
        context=context,
        prompt_version="cc_reverse:v2.1.81",
    )


# ─── BackgroundAgentHandle ───────────────────────────────────────────────────


def test_handle_creation():
    state = AgentState(phase="running")
    handle = BackgroundAgentHandle(agent_id="bg-001", task_id="task-1", state=state)
    assert handle.agent_id == "bg-001"
    assert handle.task_id == "task-1"
    assert handle.state.phase == "running"


@pytest.mark.asyncio
async def test_handle_cancel():
    state = AgentState(phase="running")
    handle = BackgroundAgentHandle(agent_id="bg-001", task_id=None, state=state)

    async def dummy():
        await asyncio.sleep(10)

    handle._task = asyncio.create_task(dummy())
    await handle.cancel()
    assert state.phase == "cancelled"
    assert handle._task.cancelled() or handle._task.done()


@pytest.mark.asyncio
async def test_handle_send_message():
    state = AgentState()
    handle = BackgroundAgentHandle(agent_id="bg-001", task_id=None, state=state)
    await handle.send_message({"action": "test"})
    msg = await asyncio.wait_for(handle.inbox.get(), timeout=0.5)
    assert msg == {"action": "test"}


@pytest.mark.asyncio
async def test_handle_wait_done():
    state = AgentState()
    handle = BackgroundAgentHandle(agent_id="bg-001", task_id=None, state=state)
    await handle.outbox.put({"type": "done", "content": "hello"})
    result = await handle._wait_done()
    assert result == "hello"


@pytest.mark.asyncio
async def test_handle_wait_error():
    state = AgentState()
    handle = BackgroundAgentHandle(agent_id="bg-001", task_id=None, state=state)
    await handle.outbox.put({"type": "error", "error": "boom"})
    result = await handle._wait_done()
    assert result == "boom"


# ─── Agent.spawn_background ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_spawn_background_returns_handle():
    agent = _make_agent()

    child = _make_agent()
    child.run = AsyncMock(return_value="mock result")
    agent.spawn_child = lambda *a, **kw: child

    handle = agent.spawn_background("test prompt", task_id="t1")
    assert isinstance(handle, BackgroundAgentHandle)
    assert handle.task_id == "t1"
    assert isinstance(child.emitter, QueueEmitter)

    result = await asyncio.wait_for(handle._wait_done(), timeout=1.0)
    assert result == "mock result"


@pytest.mark.asyncio
async def test_spawn_background_inherits_env_vars():
    agent = _make_agent(env_vars={"FOO": "bar"})
    child = _make_agent()
    child.run = AsyncMock(return_value="done")

    def mock_spawn(*a, **kw):
        # 模拟真实 spawn_child 的行为：继承父 agent 的 env_vars 并合并传入的 env_vars
        child.context.env_vars = dict(agent.context.env_vars)
        if kw.get("env_vars"):
            child.context.env_vars.update(kw["env_vars"])
        return child

    agent.spawn_child = mock_spawn
    handle = agent.spawn_background("prompt", env_vars={"BAZ": "qux"})
    await asyncio.wait_for(handle._wait_done(), timeout=1.0)
    assert child.context.env_vars == {"FOO": "bar", "BAZ": "qux"}


# ─── AgentScheduler ──────────────────────────────────────────────────────────


def test_scheduler_set_parent():
    session = MagicMock()
    sched = AgentScheduler(session)
    agent = _make_agent()
    sched.set_parent(agent)
    assert sched._parent_agent is agent


def test_scheduler_spawn_requires_parent():
    session = MagicMock()
    sched = AgentScheduler(session)
    with pytest.raises(RuntimeError, match="parent agent not set"):
        sched.spawn("prompt")


@pytest.mark.asyncio
async def test_scheduler_spawn_and_get():
    session = MagicMock()
    sched = AgentScheduler(session)
    agent = _make_agent()

    child = _make_agent()
    child.run = AsyncMock(return_value="result")
    agent.spawn_child = lambda *a, **kw: child

    sched.set_parent(agent)
    handle = sched.spawn("prompt", task_id="t1")
    assert sched.get(handle.agent_id) is handle

    result = await asyncio.wait_for(handle._wait_done(), timeout=1.0)
    assert result == "result"


@pytest.mark.asyncio
async def test_scheduler_list_all():
    session = MagicMock()
    sched = AgentScheduler(session)
    agent = _make_agent()

    # 每次 spawn 返回不同的 child，确保 agent_id 唯一
    def mock_spawn(*a, **kw):
        c = _make_agent()
        c.run = AsyncMock(return_value="result")
        return c

    agent.spawn_child = mock_spawn

    sched.set_parent(agent)
    h1 = sched.spawn("p1")
    h2 = sched.spawn("p2")
    assert len(sched.list_all()) == 2
    assert h1 in sched.list_all()
    assert h2 in sched.list_all()

    # 等待后台任务完成，避免未等待协程的警告
    await asyncio.wait_for(h1._wait_done(), timeout=1.0)
    await asyncio.wait_for(h2._wait_done(), timeout=1.0)


@pytest.mark.asyncio
async def test_scheduler_cancel():
    session = MagicMock()
    sched = AgentScheduler(session)
    agent = _make_agent()

    async def slow_run(prompt, outbox=None):
        await asyncio.sleep(10)
        return "done"

    child = _make_agent()
    child.run = slow_run
    agent.spawn_child = lambda *a, **kw: child

    sched.set_parent(agent)
    handle = sched.spawn("prompt")
    ok = sched.cancel(handle.agent_id)
    assert ok is True
    # give event loop a chance to process cancellation
    await asyncio.sleep(0.05)
    assert handle.state.phase == "cancelled"


@pytest.mark.asyncio
async def test_teammate_idle_loop():
    """
    is_teammate=True 时，后台 Agent 在初始任务完成后应进入 idle 状态，
    等待 inbox 中的 new_task 消息并继续处理。
    """
    from ccserver.team.registry import TeamRegistry
    from ccserver.team.models import TeamMemberState

    agent = _make_agent()
    # 启用 team feature
    agent.session.settings.user_agent_team = True
    agent.session.team_registry = TeamRegistry()
    agent.session.team_registry.create_team("dev", lead_name="lead")
    agent.session.team_registry.add_member("dev", "builder")

    call_count = 0

    async def fake_run(prompt, outbox=None):
        nonlocal call_count
        call_count += 1
        return f"result-{call_count}"

    child = _make_agent()
    child.run = fake_run

    def mock_spawn_child(prompt, agent_def=None, agent_name=None, **kw):
        # 模拟真实 spawn_child 对 agent_id_override 的处理
        if kw.get("agent_id_override"):
            child.context.agent_id = kw["agent_id_override"]
        return child

    agent.spawn_child = mock_spawn_child

    handle = agent.spawn_background(
        "first prompt",
        agent_id_override="builder@dev",
        is_teammate=True,
    )

    # 等待初始任务完成
    result1 = await asyncio.wait_for(handle._wait_done(), timeout=1.0)
    assert result1 == "result-1"
    assert call_count == 1

    # 验证 teammate 已进入 IDLE 状态
    member = agent.session.team_registry.get_team("dev").members["builder@dev"]
    assert member.state == TeamMemberState.IDLE

    # 向 inbox 发送 new_task 消息
    await handle.send_message({
        "msg_type": "new_task",
        "task_id": "task-2",
        "task_prompt": "second prompt",
    })

    # 等待第二次任务完成（通过轮询 call_count，避免与 forward_agent_events 竞争 outbox）
    for _ in range(50):
        await asyncio.sleep(0.02)
        if call_count >= 2:
            break
    assert call_count == 2
    assert member.state == TeamMemberState.IDLE

    # 发送 shutdown_request
    await handle.send_message({"msg_type": "shutdown_request"})
    # shutdown 后等待后台协程真正结束
    for _ in range(50):
        await asyncio.sleep(0.02)
        if handle._task.done():
            break
    assert handle._task.done()
