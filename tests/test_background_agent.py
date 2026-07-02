"""
tests/test_background_agent.py — 后台 Agent 框架单元测试

覆盖：
  - BackgroundAgentHandle 创建与取消
  - Agent.spawn_background() 返回 handle
  - AgentScheduler spawn/get/cancel/list
  - 环境变量继承

注意：forward_agent_events / outbox / _wait_done 已在 EventBus 重构中删除，
原有测试移入 test_agent_handle.py 和 test_event_bus.py 覆盖。
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from ccserver.agent import Agent, AgentContext, AgentState
from ccserver.agent_handle import BackgroundAgentHandle
from ccserver.agent_scheduler import AgentScheduler
from ccserver.messages import UnifiedToolCall


def _make_session():
    session = MagicMock()
    session.settings = MagicMock()
    session.settings.denied_tools = frozenset()
    session.settings.allowed_tools = None
    session.settings.run_mode = "auto"
    # Agent 现读 session.config；用真实 CcServerConfig（run_mode=auto）
    from ccserver.configuration.schema import CcServerConfig
    session.config = CcServerConfig.from_dict({"agent": {"run_mode": "auto"}})
    session.mcp.schemas.return_value = []
    session.skills = MagicMock()
    session.project_root = Path("/tmp/test")
    session.messages = []

    # Mock EventBus：subscribe 返回一个异步上下文管理器，get() 短暂等待后返回 None
    # 这样 _forward_bus_events 在 handle._task 结束后能快速退出
    class MockSub:
        async def get(self, timeout=None):
            await asyncio.sleep(0.001)
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self.get()

    class MockSubscription:
        async def __aenter__(self):
            return MockSub()

        async def __aexit__(self, *args):
            pass

    session.event_bus = MagicMock()
    session.event_bus.subscribe = lambda *a, **kw: MockSubscription()
    session.event_bus.publish = AsyncMock()

    # Mock hooks：emit / emit_void 返回安全的空值
    session.hooks = MagicMock()
    session.hooks.emit = AsyncMock(return_value=MagicMock(
        message=None,
        additional_context=None,
        system_message=None,
        updated_input=None,
        block=False,
        block_reason=None,
        permission_behavior="passthrough",
    ))
    session.hooks.emit_void = AsyncMock(return_value=None)
    return session


def _make_agent(tools=None, env_vars=None) -> Agent:
    session = _make_session()
    context = AgentContext(name="test-agent", messages=[], depth=0, env_vars=env_vars or {})
    # emitter 方法需要 AsyncMock（emit_tool_start / emit_tool_result / emit_done 等）
    emitter = MagicMock()
    emitter.emit_token = AsyncMock()
    emitter.emit_tool_start = AsyncMock()
    emitter.emit_tool_result = AsyncMock()
    emitter.emit_done = AsyncMock()
    emitter.emit_subagent_done = AsyncMock()
    emitter.emit_error = AsyncMock()
    emitter.emit_permission_request = AsyncMock(return_value=False)
    emitter.emit_ask_user = AsyncMock(return_value="")
    emitter.emit_compact = AsyncMock()
    return Agent(
        session=session,
        adapter=MagicMock(),
        emitter=emitter,
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


# ─── Agent.spawn_background ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_spawn_background_returns_handle():
    agent = _make_agent()

    child = _make_agent()

    async def mock_run_stream(prompt):
        # P2: spawn_background 改用 run_stream()，mock 为空生成器
        if False:
            yield None

    child.run_stream = mock_run_stream
    agent.spawn_child = lambda *a, **kw: child

    handle = agent.spawn_background("test prompt", task_id="t1")
    assert isinstance(handle, BackgroundAgentHandle)
    assert handle.task_id == "t1"
    # P2: run_stream() 内部临时替换 emitter 为 BusEmitter，
    # spawn_background 不再直接修改 child.emitter

    # 等待后台任务完成
    assert handle._task is not None
    await asyncio.wait_for(handle._task, timeout=1.0)


@pytest.mark.asyncio
async def test_spawn_background_inherits_env_vars():
    agent = _make_agent(env_vars={"FOO": "bar"})
    child = _make_agent()

    async def mock_run_stream(prompt):
        # P2: spawn_background 改用 run_stream()
        if False:
            yield None

    child.run_stream = mock_run_stream

    def mock_spawn(*a, **kw):
        # 模拟真实 spawn_child 的行为：继承父 agent 的 env_vars 并合并传入的 env_vars
        child.context.env_vars = dict(agent.context.env_vars)
        if kw.get("env_vars"):
            child.context.env_vars.update(kw["env_vars"])
        return child

    agent.spawn_child = mock_spawn
    handle = agent.spawn_background("prompt", env_vars={"BAZ": "qux"})

    # 等待后台任务完成
    assert handle._task is not None
    await asyncio.wait_for(handle._task, timeout=1.0)
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

    async def mock_run_stream(prompt):
        # P2: spawn_background 改用 run_stream()
        if False:
            yield None

    child.run_stream = mock_run_stream
    agent.spawn_child = lambda *a, **kw: child

    sched.set_parent(agent)
    handle = sched.spawn("prompt", task_id="t1")
    assert sched.get(handle.agent_id) is handle

    # 等待后台任务完成
    assert handle._task is not None
    await asyncio.wait_for(handle._task, timeout=1.0)


@pytest.mark.asyncio
async def test_scheduler_list_all():
    session = MagicMock()
    sched = AgentScheduler(session)
    agent = _make_agent()

    # 每次 spawn 返回不同的 child，确保 agent_id 唯一
    def mock_spawn(*a, **kw):
        c = _make_agent()

        async def mock_run_stream(prompt):
            # P2: spawn_background 改用 run_stream()
            if False:
                yield None

        c.run_stream = mock_run_stream
        return c

    agent.spawn_child = mock_spawn

    sched.set_parent(agent)
    h1 = sched.spawn("p1")
    h2 = sched.spawn("p2")
    assert len(sched.list_all()) == 2
    assert h1 in sched.list_all()
    assert h2 in sched.list_all()

    # 等待后台任务完成，避免未等待协程的警告
    assert h1._task is not None
    assert h2._task is not None
    await asyncio.wait_for(h1._task, timeout=1.0)
    await asyncio.wait_for(h2._task, timeout=1.0)


@pytest.mark.asyncio
async def test_scheduler_cancel():
    session = MagicMock()
    sched = AgentScheduler(session)
    agent = _make_agent()

    async def slow_run_stream(prompt):
        # P2: spawn_background 改用 run_stream()
        await asyncio.sleep(10)
        if False:
            yield None

    child = _make_agent()
    child.run_stream = slow_run_stream
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

    async def fake_run_stream(prompt):
        nonlocal call_count
        call_count += 1
        if False:
            yield None

    child = _make_agent()
    child.run_stream = fake_run_stream

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

    # 等待初始任务完成（teammate 会进入 idle 循环，不会立即结束）
    # 轮询 call_count 确认初始任务已完成
    for _ in range(50):
        await asyncio.sleep(0.02)
        if call_count >= 1:
            break
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

    # 等待第二次任务完成（通过轮询 call_count）
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


# ─── 并行 Agent 工具执行 ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_tools_parallels_multiple_agent_calls():
    """
    验证：当 LLM 返回多个 Agent tool_use 块时，_handle_tools 使用 asyncio.gather
    并行执行，而不是串行 await。

    测试策略：
      - 构造 4 个 Agent tool_use 块
      - 每个 _handle_agent 引入不同延迟（0/0.05/0.1/0.15s）
      - 串行执行总耗时应 >= 0.15s；并行执行应 < 0.1s
      - 用时间差验证确实是并行执行
    """
    import time

    agent = _make_agent()

    # 记录各 Agent 子任务的执行顺序和完成时间
    execution_log: list[tuple[str, float]] = []  # (name, timestamp)

    def make_slow_child(name: str, delay: float):
        """构造一个子 Agent，其 _loop 引入指定延迟。spawn_child 同步返回。"""
        child = _make_agent()
        child.context.name = name
        child.context.agent_id = f"child-{name}"

        async def fake_loop():
            await asyncio.sleep(delay)
            execution_log.append((name, time.monotonic()))
            return f"result of {name}"

        child._loop = fake_loop
        return child

    # 注入 mock spawn_child（同步方法，spawn_child 本身是同步的）
    def mock_spawn_child(prompt, agent_def=None, agent_name=None, model_override=None, **kw):
        name = agent_name or prompt[:10]
        delay_map = {"agent-a": 0.15, "agent-b": 0.10, "agent-c": 0.05, "agent-d": 0.00}
        delay = delay_map.get(name, 0.0)
        return make_slow_child(name, delay)

    agent.spawn_child = mock_spawn_child

    # 构造 4 个 Agent tool_use 块（模拟 LLM 返回，使用 UnifiedToolCall）
    blocks = [
        UnifiedToolCall(
            id="tool-1",
            name="Agent",
            input={
                "prompt": "agent-a prompt",
                "description": "agent-a",
            },
        ),
        UnifiedToolCall(
            id="tool-2",
            name="Agent",
            input={
                "prompt": "agent-b prompt",
                "description": "agent-b",
            },
        ),
        UnifiedToolCall(
            id="tool-3",
            name="Agent",
            input={
                "prompt": "agent-c prompt",
                "description": "agent-c",
            },
        ),
        UnifiedToolCall(
            id="tool-4",
            name="Agent",
            input={
                "prompt": "agent-d prompt",
                "description": "agent-d",
            },
        ),
    ]

    start = time.monotonic()
    results, trigger_compact = await agent._handle_tools(blocks)
    elapsed = time.monotonic() - start

    # 1. 所有 4 个结果都应返回
    assert len(results) == 4, f"expected 4 results, got {len(results)}"
    assert not trigger_compact

    # 2. 总耗时应小于串行执行的理论耗时（4 × 0.15s = 0.60s），证明是并行
    #   实际并行耗时 ≈ 最慢 agent 延迟（0.15s）+ spawn 同步开销（~0.02s）
    #   串行耗时 ≈ 0.15 + 0.10 + 0.05 + 0.00 = 0.30s
    #   设阈值为 0.20s：并行应 < 0.20s，串行应 > 0.28s
    assert elapsed < 0.20, (
        f"parallel execution took {elapsed:.3f}s — expected < 0.20s. "
        "Multiple Agent tools may be executed sequentially instead of in parallel."
    )

    # 3. execution_log 顺序应反映快速任务先完成（agent-d 最快，其次 agent-c/b/a）
    names_in_order = [name for name, _ in execution_log]
    # agent-d (0.00s) 应最先完成，agent-a (0.15s) 应最后完成
    assert names_in_order[0] == "agent-d", f"expected agent-d first, got {names_in_order}"
    assert names_in_order[-1] == "agent-a", f"expected agent-a last, got {names_in_order}"

    # 4. results 中应包含各子 agent 的结果（R3 S4：handle 返回 ToolResultBlock，content 字段）
    contents = [r.content for r in results if r and isinstance(r.content, str)]
    assert any("agent-a" in c for c in contents)
    assert any("agent-d" in c for c in contents)


@pytest.mark.asyncio
async def test_handle_tools_single_agent_no_gather_overhead():
    """
    验证：只有一个 Agent 工具块时，走快速路径（直接 await），不引入 gather 开销。
    """
    agent = _make_agent()

    child_completed = False

    def make_child():
        nonlocal child_completed
        child = _make_agent()
        child.context.agent_id = "child-single"

        async def fake_loop():
            nonlocal child_completed
            await asyncio.sleep(0.05)
            child_completed = True
            return "single result"

        child._loop = fake_loop
        return child

    # spawn_child 签名：prompt, agent_def=None, agent_name=None, model_override=None, ...
    # 使用 **kw 兼容实际签名
    agent.spawn_child = lambda prompt, **kw: make_child()

    blocks = [
        UnifiedToolCall(
            id="tool-single",
            name="Agent",
            input={"prompt": "single prompt", "description": "single"},
        ),
    ]

    results, _ = await agent._handle_tools(blocks)

    assert len(results) == 1
    assert child_completed
    # R3 S4：handle 返回 ToolResultBlock（类型化），按 content 属性断言
    assert "single result" in (results[0].content or "")
