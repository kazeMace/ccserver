"""
tests/test_command_registry.py — Agent 层命令注册表测试（P2 command_registry）。

覆盖：
  - @register 装饰器注册处理器
  - get_handler 命中和未命中
  - list_builtin_names 返回正确列表
  - /clear 处理器清空消息
  - /model 处理器显示/切换模型
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ─── register / get_handler / list_builtin_names ─────────────────────────────

class TestCommandRegistryBasic:

    def test_builtin_clear_registered(self):
        """默认应已注册 clear 命令。"""
        from ccserver.agent.command_registry import get_handler
        handler = get_handler("clear")
        assert handler is not None

    def test_builtin_model_registered(self):
        """默认应已注册 model 命令。"""
        from ccserver.agent.command_registry import get_handler
        handler = get_handler("model")
        assert handler is not None

    def test_get_handler_nonexistent_returns_none(self):
        """未注册的命令 get_handler 返回 None。"""
        from ccserver.agent.command_registry import get_handler
        assert get_handler("nonexistent_cmd_xyz") is None

    def test_list_builtin_names_contains_defaults(self):
        """list_builtin_names 应包含 clear 和 model。"""
        from ccserver.agent.command_registry import list_builtin_names
        names = list_builtin_names()
        assert "clear" in names
        assert "model" in names

    def test_register_duplicate_raises(self):
        """重复注册同名命令应断言失败。"""
        import ccserver.agent.command_registry as reg

        with pytest.raises(AssertionError):
            # 尝试再次注册已存在的 clear，应该报错
            decorator = reg.register("clear")
            decorator(AsyncMock())


# ─── /clear 处理器 ────────────────────────────────────────────────────────────

class TestClearHandler:

    @pytest.mark.anyio
    async def test_clear_clears_messages(self):
        """/clear 应清空 context.messages。"""
        from ccserver.agent.command_registry import get_handler

        agent = MagicMock()
        agent.context.messages = [{"role": "user", "content": "hello"}]
        agent.persist = False

        handler = get_handler("clear")
        result = await handler(agent, "")

        assert agent.context.messages == []
        assert result == ""  # stdout 为空

    @pytest.mark.anyio
    async def test_clear_persists_when_persist_true(self):
        """/clear 在 persist=True 时应调用 session.rewrite_messages([])。"""
        from ccserver.agent.command_registry import get_handler

        agent = MagicMock()
        agent.context.messages = [{"role": "user", "content": "hello"}]
        agent.persist = True

        handler = get_handler("clear")
        await handler(agent, "")

        agent.session.rewrite_messages.assert_called_once_with([])

    @pytest.mark.anyio
    async def test_clear_no_persist_skips_rewrite(self):
        """/clear 在 persist=False 时不调用 rewrite_messages。"""
        from ccserver.agent.command_registry import get_handler

        agent = MagicMock()
        agent.context.messages = []
        agent.persist = False

        handler = get_handler("clear")
        await handler(agent, "")

        agent.session.rewrite_messages.assert_not_called()


# ─── /model 处理器 ────────────────────────────────────────────────────────────

class TestModelHandler:

    @pytest.mark.anyio
    async def test_model_no_args_shows_current(self):
        """/model 无参数时返回当前模型。"""
        from ccserver.agent.command_registry import get_handler

        agent = MagicMock()
        agent.session.config.model.model_id = "claude-opus-4-8"

        handler = get_handler("model")
        result = await handler(agent, "")

        assert "claude-opus-4-8" in result

    @pytest.mark.anyio
    async def test_model_with_args_switches_model(self):
        """/model <name> 应切换模型 ID。"""
        from ccserver.agent.command_registry import get_handler

        agent = MagicMock()
        agent.session.config.model.model_id = "claude-sonnet-4-6"
        agent.context.agent_id = "test-agent-id"

        handler = get_handler("model")
        result = await handler(agent, "claude-haiku-4-5")

        assert agent.session.config.model.model_id == "claude-haiku-4-5"
        assert "claude-haiku-4-5" in result

    @pytest.mark.anyio
    async def test_model_switch_result_shows_old_and_new(self):
        """/model 切换后的返回值应包含旧模型和新模型名。"""
        from ccserver.agent.command_registry import get_handler

        agent = MagicMock()
        agent.session.config.model.model_id = "old-model"
        agent.context.agent_id = "test-agent-id"

        handler = get_handler("model")
        result = await handler(agent, "new-model")

        assert "old-model" in result
        assert "new-model" in result
