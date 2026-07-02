"""
tests/test_gateway_commands.py — GatewayCommandHandler 测试。

覆盖：
  - is_gateway_command 判断
  - /stop：有 Agent 时中断，无 Agent 时返回提示
  - /new：清空历史、让缓存失效、返回标题
  - /reset：/new 别名
  - /status：返回 Agent 状态和 channel 信息
  - /help：返回分组命令列表
  - 非 Gateway 命令返回 handled=False
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from ccserver.channels.gateway_commands import (
    GatewayCommandHandler,
    GatewayCommandResult,
    GATEWAY_COMMANDS,
)


def make_handler(session=None, lifecycle=None, runner=None):
    """辅助：构建 GatewayCommandHandler + 默认 mock 依赖。"""
    session_manager = MagicMock()
    session_manager.get = MagicMock(return_value=session)

    if lifecycle is None:
        lc = MagicMock()
        lc.list_running.return_value = []
    else:
        lc = lifecycle  # 使用调用方传入的 lifecycle，不覆盖已设置的 return_value

    return GatewayCommandHandler(session_manager, lc, runner=runner)


# ─── is_gateway_command ──────────────────────────────────────────────────────

class TestIsGatewayCommand:

    def test_stop_is_gateway(self):
        h = make_handler()
        assert h.is_gateway_command("/stop") is True

    def test_new_is_gateway(self):
        h = make_handler()
        assert h.is_gateway_command("/new") is True

    def test_reset_is_gateway(self):
        h = make_handler()
        assert h.is_gateway_command("/reset") is True

    def test_status_is_gateway(self):
        h = make_handler()
        assert h.is_gateway_command("/status") is True

    def test_help_is_gateway(self):
        h = make_handler()
        assert h.is_gateway_command("/help") is True

    def test_uppercase_still_detected(self):
        h = make_handler()
        assert h.is_gateway_command("/STOP") is True

    def test_normal_message_not_gateway(self):
        h = make_handler()
        assert h.is_gateway_command("hello") is False

    def test_user_custom_command_not_gateway(self):
        h = make_handler()
        assert h.is_gateway_command("/myskill") is False

    def test_empty_string_not_gateway(self):
        h = make_handler()
        assert h.is_gateway_command("") is False


# ─── /stop ───────────────────────────────────────────────────────────────────

class TestHandleStop:

    @pytest.mark.anyio
    async def test_stop_with_running_agent(self):
        """有运行中的 Agent 时应调用 interrupt() 并返回提示。"""
        mock_agent = MagicMock()
        session = MagicMock()
        session.root_agent = mock_agent

        h = make_handler(session=session)
        result = await h.handle("/stop", "sess-key")

        assert result.handled is True
        assert "中断" in result.reply
        mock_agent.interrupt.assert_called_once()

    @pytest.mark.anyio
    async def test_stop_without_agent(self):
        """无运行中的 Agent 时应返回提示但不崩溃。"""
        session = MagicMock()
        session.root_agent = None

        h = make_handler(session=session)
        result = await h.handle("/stop", "sess-key")

        assert result.handled is True
        assert result.reply is not None

    @pytest.mark.anyio
    async def test_stop_without_session(self):
        """Session 不存在时应 handled=True，不崩溃。"""
        h = make_handler(session=None)
        result = await h.handle("/stop", "nonexistent")
        assert result.handled is True


# ─── /new ────────────────────────────────────────────────────────────────────

class TestHandleNew:

    @pytest.mark.anyio
    async def test_new_clears_messages(self):
        """应调用 session.rewrite_messages([]) 清空历史。"""
        session = MagicMock()
        session.root_agent = None

        h = make_handler(session=session)
        await h.handle("/new", "sess-key")

        session.rewrite_messages.assert_called_once_with([])

    @pytest.mark.anyio
    async def test_new_with_title(self):
        """/new <title> 应在回复中包含标题。"""
        session = MagicMock()
        session.root_agent = None

        h = make_handler(session=session)
        result = await h.handle("/new 新项目分析", "sess-key")

        assert "新项目分析" in result.reply

    @pytest.mark.anyio
    async def test_new_without_title_uses_default(self):
        """/new 无参数时使用默认标题。"""
        session = MagicMock()
        session.root_agent = None

        h = make_handler(session=session)
        result = await h.handle("/new", "sess-key")

        assert result.reply is not None

    @pytest.mark.anyio
    async def test_new_interrupts_running_agent(self):
        """有运行中的 Agent 时 /new 应先中断它。"""
        mock_agent = MagicMock()
        session = MagicMock()
        session.root_agent = mock_agent

        h = make_handler(session=session)
        await h.handle("/new", "sess-key")

        mock_agent.interrupt.assert_called_once()

    @pytest.mark.anyio
    async def test_new_invalidates_agent_cache(self):
        """有 runner 时 /new 应调用 runner.invalidate_agent()。"""
        session = MagicMock()
        session.root_agent = None

        runner = MagicMock()
        runner.invalidate_agent = MagicMock(return_value=True)

        h = make_handler(session=session, runner=runner)
        await h.handle("/new", "sess-key")

        runner.invalidate_agent.assert_called_once_with(session.id)

    @pytest.mark.anyio
    async def test_reset_is_alias_for_new(self):
        """/reset 应等价于 /new。"""
        session = MagicMock()
        session.root_agent = None

        h = make_handler(session=session)
        result = await h.handle("/reset", "sess-key")

        assert result.handled is True
        session.rewrite_messages.assert_called_once_with([])


# ─── /status ─────────────────────────────────────────────────────────────────

class TestHandleStatus:

    @pytest.mark.anyio
    async def test_status_no_session(self):
        """无 session 时返回"无活跃会话"。"""
        h = make_handler(session=None)
        result = await h.handle("/status", "nonexistent")

        assert result.handled is True
        assert "无活跃会话" in result.reply

    @pytest.mark.anyio
    async def test_status_with_idle_agent(self):
        """Agent 为 None 时返回"空闲"。"""
        session = MagicMock()
        session.root_agent = None

        h = make_handler(session=session)
        result = await h.handle("/status", "sess-key")

        assert result.handled is True
        assert "空闲" in result.reply

    @pytest.mark.anyio
    async def test_status_shows_channels(self):
        """结果中包含 channel 信息。"""
        session = MagicMock()
        session.root_agent = None

        lc = MagicMock()
        lc.list_running.return_value = [
            {"channel_id": "discord", "account_id": "bot1"},
        ]

        h = make_handler(session=session, lifecycle=lc)
        result = await h.handle("/status", "sess-key")

        # channel_info 格式："discord:bot1"
        assert "discord" in result.reply
        assert "bot1" in result.reply


# ─── /help ───────────────────────────────────────────────────────────────────

class TestHandleHelp:

    @pytest.mark.anyio
    async def test_help_contains_gateway_commands(self):
        """/help 应包含所有 Gateway 层命令。"""
        session = MagicMock()
        session.commands.list_commands.return_value = []

        h = make_handler(session=session)
        result = await h.handle("/help", "sess-key")

        assert result.handled is True
        for cmd in ["/stop", "/new", "/reset", "/status", "/help"]:
            assert cmd in result.reply

    @pytest.mark.anyio
    async def test_help_shows_user_commands(self):
        """/help 应包含用户自定义命令（来自 CommandLoader）。"""
        session = MagicMock()
        session.commands.list_commands.return_value = [
            {"name": "/persona", "description": "切换人设"},
        ]

        h = make_handler(session=session)
        result = await h.handle("/help", "sess-key")

        assert "/persona" in result.reply


# ─── 非 Gateway 命令 ──────────────────────────────────────────────────────────

class TestNonGatewayCommand:

    @pytest.mark.anyio
    async def test_unknown_command_handled_false(self):
        """未知 / 字命令返回 handled=False，调用方走 Agent 路径。"""
        h = make_handler()
        result = await h.handle("/myskill", "sess-key")
        assert result.handled is False


# ─── GATEWAY_COMMANDS 集合完整性 ─────────────────────────────────────────────

def test_gateway_commands_set_complete():
    """GATEWAY_COMMANDS 集合包含所有预期的命令名（不含 /）。"""
    assert GATEWAY_COMMANDS == {"stop", "new", "reset", "status", "help"}
