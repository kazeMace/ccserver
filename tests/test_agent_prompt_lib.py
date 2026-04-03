# tests/test_agent_prompt_version.py
from unittest.mock import MagicMock, AsyncMock
from ccserver.agent import Agent, AgentContext


def _make_agent(prompt_version="cc_reverse:v2.1.81", tmp_path=None):
    from pathlib import Path
    session = MagicMock()
    session.workdir = "/tmp"
    session.messages = []
    session.id = "test-id-12345678"
    # project_root 必须是真实 Path，lib.py 会做 / 运算访问 CLAUDE.md
    session.project_root = tmp_path or Path("/tmp")
    session.mcp.schemas.return_value = []
    emitter = MagicMock()
    emitter.emit_token = AsyncMock()
    emitter.emit_done = AsyncMock()
    emitter.emit_error = AsyncMock()

    return Agent(
        session=session,
        adapter=MagicMock(),
        emitter=emitter,
        tools={},
        context=AgentContext(name="test", messages=session.messages, depth=0),
        prompt_version=prompt_version,
    )


def test_agent_has_prompt_version(tmp_path):
    agent = _make_agent("cc_reverse:v2.1.81", tmp_path)
    assert agent.prompt_version == "cc_reverse:v2.1.81"


def test_spawn_child_inherits_prompt_version(tmp_path):
    agent = _make_agent("cc_reverse:v2.1.81", tmp_path)
    child = agent.spawn_child("do something")
    assert child.prompt_version == "cc_reverse:v2.1.81"


def test_spawn_child_can_override_prompt_version(tmp_path):
    agent = _make_agent("cc_reverse:v2.1.81", tmp_path)
    child = agent.spawn_child("do something", prompt_version="cc_reverse:v2.1.81")
    assert child.prompt_version == "cc_reverse:v2.1.81"


def test_append_wraps_string_user_message(tmp_path):
    """user message 是字符串时，_append 会调用 lib.build_user_message 包装成 list"""
    agent = _make_agent("cc_reverse:v2.1.81", tmp_path)
    agent._append({"role": "user", "content": "hello"})
    # context.messages 应该有一条消息，content 是 list（被 lib 包装后）
    assert len(agent.context.messages) == 1
    msg = agent.context.messages[0]
    assert msg["role"] == "user"
    assert isinstance(msg["content"], list)


def test_append_does_not_rewrap_list_user_message(tmp_path):
    """user message 已经是 list 时，_append 不再包装"""
    agent = _make_agent("cc_reverse:v2.1.81", tmp_path)
    content = [{"type": "text", "text": "already a list"}]
    agent._append({"role": "user", "content": content})
    msg = agent.context.messages[0]
    assert msg["content"] is content  # 同一个对象，未被替换
