"""
tests/test_agent_handle_tools.py — _handle_agent 工具路由测试。

覆盖：
  - Agent(..., run_in_background=False) 走同步路径（await child._loop()）
  - Agent(..., run_in_background=True)  走后台路径（spawn_background 立即返回）
  - run_in_background=True 时返回 agent_task_id，主 Agent 不被阻塞
  - 后台 Agent 完成后 _notify_parent_done 注入消息到父 Agent messages
  - 深度超限时报错，空 prompt 时报错

测试策略：构造真实的 Agent 实例（mock session/context 依赖），
对被测方法使用 patch.object(target, name)。
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from pathlib import Path


# ─── 构造最小 Agent 实例 ─────────────────────────────────────────────────────


def make_agent(context_depth: int = 0):
    """构造一个最小化的 Agent 实例。"""
    from ccserver.agent import Agent, AgentContext

    session = MagicMock()
    session.id = "test-session"
    session.hooks.emit_void = AsyncMock()
    session.hooks.emit = AsyncMock()
    session.hooks.emit.return_value = MagicMock(block=False, updated_input=None)
    session.agents.get = MagicMock(return_value=None)
    session.mcp = MagicMock()
    session.mcp.schemas = MagicMock(return_value=[])
    session.settings = MagicMock()
    session.settings.allowed_tools = None
    session.settings.denied_tools = {}
    session.settings.ask_tools = []
    session.settings.run_mode = None
    session.settings.is_command_allowed = MagicMock(return_value=True)
    session.shell_tasks = MagicMock()
    session.storage = MagicMock()
    session.storage.append_message = MagicMock()

    context = AgentContext(
        agent_id="test-agent-id",
        depth=context_depth,
        messages=[],
    )

    emitter = MagicMock()
    emitter.emit = AsyncMock()
    emitter.emit_done = AsyncMock()
    emitter.emit_token = AsyncMock()
    emitter.emit_tool_start = AsyncMock()
    emitter.emit_tool_result = AsyncMock()
    emitter.emit_error = AsyncMock()
    emitter.emit_ask_user = AsyncMock(return_value="")
    emitter.emit_permission_request = AsyncMock(return_value=True)
    emitter.emit_task_started = AsyncMock()
    emitter.emit_task_progress = AsyncMock()
    emitter.emit_task_done = AsyncMock()

    agent = Agent(
        session=session,
        adapter=MagicMock(),
        emitter=emitter,
        tools={},
        context=context,
        round_limit=5,
    )
    return agent


# ─── 测试用例 ───────────────────────────────────────────────────────────────


class TestHandleAgentSyncPath:
    """Agent(..., run_in_background=False) 同步路径。"""

    @pytest.mark.anyio
    async def test_empty_prompt_returns_error(self):
        """prompt 为空时返回错误。"""
        agent = make_agent()
        result = await agent._handle_agent({})
        assert result.is_error
        assert "non-empty prompt" in result.content

    @pytest.mark.anyio
    async def test_sync_mode_awaits_child_loop(self):
        """run_in_background=False 时，_handle_agent 应 await child._loop()。"""
        agent = make_agent()

        mock_child = MagicMock()
        mock_child.aid_label = "child-0"
        mock_child.context.agent_id = "child-id"
        mock_child.context.name = None
        mock_child._loop = AsyncMock(return_value="sync summary")
        mock_child._build_hook_ctx = MagicMock(return_value={})

        with patch.object(agent, "spawn_child", return_value=mock_child):
            result = await agent._handle_agent({
                "prompt": "do something",
                "run_in_background": False,
            })

        mock_child._loop.assert_awaited_once()
        assert result.is_error is False
        assert "sync summary" in result.content

    @pytest.mark.anyio
    async def test_sync_mode_subagent_not_found_keeps_running(self):
        """subagent_type 不存在时仍执行 generic 子 Agent。"""
        agent = make_agent()
        agent.session.agents.get = MagicMock(return_value=None)

        mock_child = MagicMock()
        mock_child.aid_label = "child-0"
        mock_child.context.agent_id = "child-id"
        mock_child.context.name = None
        mock_child._loop = AsyncMock(return_value="ok")
        mock_child._build_hook_ctx = MagicMock(return_value={})

        with patch.object(agent, "spawn_child", return_value=mock_child):
            result = await agent._handle_agent({
                "prompt": "hello",
                "subagent_type": "nonexistent",
                "run_in_background": False,
            })

        assert result.is_error is False
        agent.session.agents.get.assert_called_once_with("nonexistent")

    @pytest.mark.anyio
    async def test_sync_mode_respects_model_override(self):
        """model 参数应传给 spawn_child。"""
        agent = make_agent()

        mock_child = MagicMock()
        mock_child.aid_label = "child"
        mock_child.context.agent_id = "child-id"
        mock_child.context.name = None
        mock_child._loop = AsyncMock(return_value="result")
        mock_child._build_hook_ctx = MagicMock(return_value={})

        with patch.object(agent, "spawn_child", return_value=mock_child) as mock_sc:
            await agent._handle_agent({
                "prompt": "use model",
                "model": "claude-haiku",
                "run_in_background": False,
            })

        assert mock_sc.call_args.kwargs["model_override"] == "claude-haiku"


class TestHandleAgentBackgroundPath:
    """Agent(..., run_in_background=True) 后台路径。"""

    @pytest.mark.anyio
    async def test_background_mode_calls_spawn_background(self):
        """run_in_background=True 时调用 spawn_background() 并立即返回。"""
        agent = make_agent()

        mock_handle = MagicMock()
        mock_handle.agent_task_id = "a1234567"
        mock_handle.agent_id = "bg-agent-id"

        with patch.object(agent, "spawn_background", return_value=mock_handle) as mock_sb:
            result = await agent._handle_agent({
                "prompt": "background task",
                "run_in_background": True,
            })

        mock_sb.assert_called_once()
        call_kwargs = mock_sb.call_args.kwargs
        assert call_kwargs["prompt"] == "background task"
        assert call_kwargs["agent_name"] == ""

        assert result.is_error is False
        assert "a1234567" in result.content
        assert "background" in result.content.lower()

    @pytest.mark.anyio
    async def test_background_mode_passes_subagent_type_as_agent_name(self):
        """subagent_type 作为 agent_name 传给 spawn_background。"""
        agent = make_agent()
        mock_handle = MagicMock()
        mock_handle.agent_task_id = "a00000st"
        mock_handle.agent_id = "bg"

        with patch.object(agent, "spawn_background", return_value=mock_handle) as mock_sb:
            await agent._handle_agent({
                "prompt": "research",
                "subagent_type": "researcher",
                "run_in_background": True,
            })

        assert mock_sb.call_args.kwargs["agent_name"] == "researcher"

    @pytest.mark.anyio
    async def test_background_mode_passes_model_override(self):
        """model 参数作为 model_override 传给 spawn_background。"""
        agent = make_agent()
        mock_handle = MagicMock()
        mock_handle.agent_task_id = "a00000mo"
        mock_handle.agent_id = "bg"

        with patch.object(agent, "spawn_background", return_value=mock_handle) as mock_sb:
            await agent._handle_agent({
                "prompt": "fast",
                "model": "claude-haiku",
                "run_in_background": True,
            })

        assert mock_sb.call_args.kwargs["model_override"] == "claude-haiku"

    @pytest.mark.anyio
    async def test_background_mode_false_still_uses_sync(self):
        """run_in_background=False 走同步路径，不调用 spawn_background。"""
        agent = make_agent()

        mock_child = MagicMock()
        mock_child.aid_label = "child"
        mock_child.context.agent_id = "child-id"
        mock_child.context.name = None
        mock_child._loop = AsyncMock(return_value="sync result")
        mock_child._build_hook_ctx = MagicMock(return_value={})

        with patch.object(agent, "spawn_child", return_value=mock_child):
            with patch.object(agent, "spawn_background") as mock_sb:
                result = await agent._handle_agent({
                    "prompt": "explicit sync",
                    "run_in_background": False,
                })

        mock_sb.assert_not_called()
        mock_child._loop.assert_awaited_once()
        assert result.is_error is False

    @pytest.mark.anyio
    async def test_background_mode_not_set_defaults_to_sync(self):
        """不传 run_in_background 参数时，默认走同步路径。"""
        agent = make_agent()

        mock_child = MagicMock()
        mock_child.aid_label = "child"
        mock_child.context.agent_id = "child-id"
        mock_child.context.name = None
        mock_child._loop = AsyncMock(return_value="default result")
        mock_child._build_hook_ctx = MagicMock(return_value={})

        with patch.object(agent, "spawn_child", return_value=mock_child):
            with patch.object(agent, "spawn_background") as mock_sb:
                result = await agent._handle_agent({"prompt": "default behavior"})

        mock_sb.assert_not_called()
        mock_child._loop.assert_awaited_once()


class TestHandleAgentDepthLimit:
    """深度限制测试。"""

    @pytest.mark.anyio
    async def test_depth_limit_returns_error_without_spawning(self):
        """context.depth >= MAX_DEPTH 时返回错误，不创建子 Agent。"""
        agent = make_agent(context_depth=10)

        with patch.object(agent, "spawn_child") as mock_sc:
            with patch.object(agent, "spawn_background") as mock_sb:
                result = await agent._handle_agent({
                    "prompt": "too deep",
                    "run_in_background": False,
                })

        mock_sc.assert_not_called()
        mock_sb.assert_not_called()
        assert result.is_error
        assert "Max agent nesting depth" in result.content


# ─── _notify_parent_done 测试 ─────────────────────────────────────────────────


class TestNotifyParentDone:
    """Agent._notify_parent_done 向父 Agent 注入完成通知消息。"""

    @pytest.mark.anyio
    async def test_injects_done_message_into_parent_messages(self):
        """正常完成时，父 Agent messages 中应包含一条 system 消息。"""
        from ccserver.agent import Agent

        parent = MagicMock()
        parent.context = MagicMock()
        parent.context.messages = []
        parent.session = MagicMock()
        parent.session.id = "test-sid"
        parent.session.storage = None
        parent.session.hooks = MagicMock()
        parent.session.hooks.emit_void = AsyncMock()
        parent.aid_label = "parent(agent)"
        parent._build_hook_ctx = MagicMock(return_value={})

        def noop(coro):
            """Fire-and-forget 模拟：直接关闭协程，不执行（hook 不在测试范围内）。"""
            coro.close()

        with patch("ccserver.agent.asyncio.create_task", side_effect=noop):
            await Agent._notify_parent_done(
                parent,
                agent_task_id="a0000001",
                agent_name="researcher",
                result="analysis complete",
            )

        assert len(parent.context.messages) == 1
        msg = parent.context.messages[0]
        assert msg["role"] == "system"
        assert "completed" in msg["content"]
        assert msg["_ccserver_background_agent_done"] is True
        assert msg["agent_task_id"] == "a0000001"

    @pytest.mark.anyio
    async def test_injects_cancelled_message(self):
        """取消时，消息内容应标注 cancelled。"""
        from ccserver.agent import Agent

        parent = MagicMock()
        parent.context = MagicMock()
        parent.context.messages = []
        parent.session = MagicMock()
        parent.session.storage = None
        parent.session.hooks = MagicMock()
        parent.session.hooks.emit_void = AsyncMock()
        parent.aid_label = "parent(agent)"
        parent._build_hook_ctx = MagicMock(return_value={})

        def noop(coro):
            coro.close()

        with patch("ccserver.agent.asyncio.create_task", side_effect=noop):
            await Agent._notify_parent_done(
                parent,
                agent_task_id="a0000002",
                agent_name="worker",
                result=None,
                cancelled=True,
            )

        assert len(parent.context.messages) == 1
        assert "cancelled" in parent.context.messages[0]["content"]

    @pytest.mark.anyio
    async def test_injects_error_message(self):
        """异常结束时，消息内容应包含错误信息。"""
        from ccserver.agent import Agent

        parent = MagicMock()
        parent.context = MagicMock()
        parent.context.messages = []
        parent.session = MagicMock()
        parent.session.storage = None
        parent.session.hooks = MagicMock()
        parent.session.hooks.emit_void = AsyncMock()
        parent.aid_label = "parent(agent)"
        parent._build_hook_ctx = MagicMock(return_value={})

        def noop(coro):
            coro.close()

        with patch("ccserver.agent.asyncio.create_task", side_effect=noop):
            await Agent._notify_parent_done(
                parent,
                agent_task_id="a0000003",
                agent_name="builder",
                result=None,
                error="LLM timeout",
            )

        assert len(parent.context.messages) == 1
        assert "failed" in parent.context.messages[0]["content"]
        assert "LLM timeout" in parent.context.messages[0]["content"]

    @pytest.mark.anyio
    async def test_truncates_long_result(self):
        """结果超过 500 字符时截断，避免污染上下文。"""
        from ccserver.agent import Agent

        parent = MagicMock()
        parent.context = MagicMock()
        parent.context.messages = []
        parent.session = MagicMock()
        parent.session.storage = None
        parent.session.hooks = MagicMock()
        parent.session.hooks.emit_void = AsyncMock()
        parent.aid_label = "parent(agent)"
        parent._build_hook_ctx = MagicMock(return_value={})

        def noop(coro):
            coro.close()

        long_result = "x" * 1000
        with patch("ccserver.agent.asyncio.create_task", side_effect=noop):
            await Agent._notify_parent_done(
                parent,
                agent_task_id="a0000004",
                agent_name="long-task",
                result=long_result,
            )

        content = parent.context.messages[0]["content"]
        assert len(content) < len(long_result)
        assert "...(truncated)" in content

    @pytest.mark.anyio
    async def test_persists_to_session_storage(self):
        """完成消息应持久化到 session storage。"""
        from ccserver.agent import Agent

        parent = MagicMock()
        parent.context = MagicMock()
        parent.context.messages = []
        parent.session = MagicMock()
        parent.session.id = "persist-sid"
        parent.session.storage = MagicMock()
        parent.session.storage.append_message = MagicMock()
        parent.session.hooks = MagicMock()
        parent.session.hooks.emit_void = AsyncMock()
        parent.aid_label = "parent(agent)"
        parent._build_hook_ctx = MagicMock(return_value={})

        def noop(coro):
            coro.close()

        with patch("ccserver.agent.asyncio.create_task", side_effect=noop):
            await Agent._notify_parent_done(
                parent,
                agent_task_id="a0000005",
                agent_name="persist-test",
                result="done",
            )

        parent.session.storage.append_message.assert_called_once()
        call_args = parent.session.storage.append_message.call_args
        assert call_args[0][0] == "persist-sid"
        assert call_args[0][1]["_ccserver_background_agent_done"] is True

    @pytest.mark.anyio
    async def test_fires_background_agent_done_hook(self):
        """完成后应触发 background_agent:done hook。"""
        from ccserver.agent import Agent

        parent = MagicMock()
        parent.context = MagicMock()
        parent.context.messages = []
        parent.session = MagicMock()
        parent.session.storage = None
        parent.session.hooks = MagicMock()
        parent.session.hooks.emit_void = AsyncMock()
        parent.aid_label = "parent(agent)"
        parent._build_hook_ctx = MagicMock(return_value={})

        def noop(coro):
            coro.close()

        with patch("ccserver.agent.asyncio.create_task", side_effect=noop):
            await Agent._notify_parent_done(
                parent,
                agent_task_id="a0000006",
                agent_name="hook-test",
                result="hook fired",
            )

        parent.session.hooks.emit_void.assert_called_once()
        call_args = parent.session.hooks.emit_void.call_args
        assert call_args[0][0] == "background_agent:done"
        assert call_args[0][1]["agent_task_id"] == "a0000006"
        assert call_args[0][1]["result"] == "hook fired"
