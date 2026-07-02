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
    # event_bus.publish 是 async，测试中需要支持 await
    session.event_bus.publish = AsyncMock(return_value=None)
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
    """user message 是字符串时，_append 经 lib 包装后统一存为 UnifiedMessage（UnifiedTextBlock）"""
    from ccserver.messages import UnifiedMessage, UnifiedTextBlock
    agent = _make_agent("cc_reverse:v2.1.81", tmp_path)
    agent._append({"role": "user", "content": "hello"})
    # R3 S4：context.messages 统一存 UnifiedMessage（content 为 list[Block]）
    assert len(agent.context.messages) == 1
    msg = agent.context.messages[0]
    assert isinstance(msg, UnifiedMessage)
    assert msg.role == "user"
    # lib 把字符串包装成 UnifiedTextBlock（单块），文本保持不变
    assert all(isinstance(b, UnifiedTextBlock) for b in msg.content)
    assert "".join(b.text for b in msg.content) == "hello"


def test_append_does_not_rewrap_list_user_message(tmp_path):
    """user message 已经是 list 时，_append 不再二次包装，内容保持等价"""
    from ccserver.messages import UnifiedMessage, UnifiedTextBlock
    agent = _make_agent("cc_reverse:v2.1.81", tmp_path)
    content = [{"type": "text", "text": "already a list"}]
    agent._append({"role": "user", "content": content})
    # R3 S4：context.messages 存 UnifiedMessage；list[text] 原样转为 UnifiedTextBlock
    msg = agent.context.messages[0]
    assert isinstance(msg, UnifiedMessage)
    assert len(msg.content) == 1
    assert isinstance(msg.content[0], UnifiedTextBlock)
    assert msg.content[0].text == "already a list"
