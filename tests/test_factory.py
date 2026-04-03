# tests/test_factory.py
from unittest.mock import MagicMock
from ccserver.factory import AgentFactory
from ccserver.agent import Agent


def _make_session():
    session = MagicMock()
    session.workdir = "/tmp"
    session.todo = MagicMock()
    session.skills = MagicMock()
    session.messages = []
    session.id = "test-session-id-12345"
    session.project_root = "/tmp/test"
    return session


def test_create_root_uses_prompt_version():
    session = _make_session()
    emitter = MagicMock()
    agent = AgentFactory.create_root(session, emitter)
    assert isinstance(agent, Agent)
    # system 应该是列表，且每项有 type 和 text
    assert isinstance(agent.system, list)
    for item in agent.system:
        assert "type" in item
        assert "text" in item


def test_create_root_custom_prompt_version():
    session = _make_session()
    emitter = MagicMock()
    agent = AgentFactory.create_root(session, emitter, prompt_version="cc_reverse:v2.1.81")
    assert isinstance(agent, Agent)
