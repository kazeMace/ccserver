"""tests/test_message_builder.py — L2 MessageBuilder（造消息 + sanitize）。"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from ccserver.agent.message_builder import MessageBuilder


# ─── sanitize_messages（纯函数，迁自旧 llm_caller）────────────────────────────


def test_sanitize_dangling_tool_use_at_end():
    """末尾悬挂 tool_use → 追加占位 tool_result。"""
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
        ]},
    ]
    fixed = MessageBuilder.sanitize_messages(messages)
    assert fixed is True
    assert messages[-1]["role"] == "user"
    assert messages[-1]["content"][0]["type"] == "tool_result"
    assert messages[-1]["content"][0]["tool_use_id"] == "t1"


def test_sanitize_broken_sequence_next_not_user():
    """tool_use 后紧跟非 user（被外部消息打断）→ 插入 tool_result。"""
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
        ]},
        {"role": "assistant", "content": "外部插入"},
    ]
    fixed = MessageBuilder.sanitize_messages(messages)
    assert fixed is True
    assert messages[1]["role"] == "user"
    assert messages[1]["content"][0]["tool_use_id"] == "t1"


def test_sanitize_wellformed_no_change():
    """完整配对不修改。"""
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
        ]},
    ]
    fixed = MessageBuilder.sanitize_messages(messages)
    assert fixed is False


# ─── build()：hook 改 system / 追加 additional_context ────────────────────────


def _make_rt(messages, system):
    """构造满足 MessageBuilder 所需的最小 rt（AgentRuntime 桩）。"""
    rt = MagicMock()
    rt.system = system
    rt.model = "m"
    rt.context = MagicMock()
    rt.context.messages = messages
    rt.session = MagicMock()
    rt.session.hooks = MagicMock()
    rt._build_hook_ctx = MagicMock(return_value=MagicMock())
    return rt


@pytest.mark.asyncio
async def test_build_uses_hook_system_message():
    """build:before hook 返回 system_message → 覆盖原 system。"""
    rt = _make_rt(messages=[{"role": "user", "content": "hi"}], system="orig-sys")
    hook_result = MagicMock()
    hook_result.system_message = "hooked-sys"
    hook_result.additional_context = None
    rt.session.hooks.emit = AsyncMock(return_value=hook_result)
    rt.session.hooks.emit_void = AsyncMock()

    builder = MessageBuilder(rt)
    system, messages = await builder.build()

    assert system == "hooked-sys"
    assert messages == [{"role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_build_appends_additional_context_to_last_user():
    """additional_context 追加到最后一条 user 消息。"""
    rt = _make_rt(messages=[{"role": "user", "content": "hi"}], system="s")
    hook_result = MagicMock()
    hook_result.system_message = None
    hook_result.additional_context = "EXTRA"
    rt.session.hooks.emit = AsyncMock(return_value=hook_result)
    rt.session.hooks.emit_void = AsyncMock()

    builder = MessageBuilder(rt)
    system, messages = await builder.build()

    assert system == "s"
    assert messages[-1]["content"] == "hi\n\nEXTRA"


@pytest.mark.asyncio
async def test_build_emits_input_hook():
    """build 末尾触发 prompt:llm:input（observing）。"""
    rt = _make_rt(messages=[{"role": "user", "content": "hi"}], system="s")
    hook_result = MagicMock()
    hook_result.system_message = None
    hook_result.additional_context = None
    rt.session.hooks.emit = AsyncMock(return_value=hook_result)
    rt.session.hooks.emit_void = AsyncMock()

    builder = MessageBuilder(rt)
    await builder.build()

    called_events = [c.args[0] for c in rt.session.hooks.emit_void.call_args_list]
    assert "prompt:llm:input" in called_events


@pytest.mark.asyncio
async def test_build_additional_context_dropped_when_last_not_user():
    """additional_context 存在但最后一条不是 user → 静默丢弃，消息不变。"""
    rt = _make_rt(
        messages=[{"role": "assistant", "content": "上一轮回答"}],
        system="s",
    )
    hook_result = MagicMock()
    hook_result.system_message = None
    hook_result.additional_context = "EXTRA"
    rt.session.hooks.emit = AsyncMock(return_value=hook_result)
    rt.session.hooks.emit_void = AsyncMock()

    builder = MessageBuilder(rt)
    system, messages = await builder.build()

    # 最后一条是 assistant，additional_context 不应被追加
    assert messages[-1]["content"] == "上一轮回答"
