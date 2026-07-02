# tests/test_factory.py
from unittest.mock import MagicMock

from ccserver.factory import AgentFactory
from ccserver.agent import Agent
from ccserver.session import SessionManager
from ccserver.configuration.loader import ProcessConfig


def _make_session(tmp_path):
    """构建真实 Session（隔离的临时 project_root），避免 mock config 的脆弱性。"""
    pc = ProcessConfig.load(global_file=tmp_path / "g.json", environ={})
    sm = SessionManager(process_config=pc, project_root=tmp_path)
    return sm.create()


async def test_create_root_uses_prompt_version(tmp_path):
    # async def：在已运行的事件循环里执行，factory 末尾 cron.start() 的 create_task 才有循环
    session = _make_session(tmp_path)
    emitter = MagicMock()
    # 传入 fake adapter，让 factory 跳过真实端点构建（不打网络）
    agent = AgentFactory.create_root(session, emitter, adapter=MagicMock())
    assert isinstance(agent, Agent)
    # 默认使用 config.agent.prompt_lib
    assert agent.prompt_version == "cc_reverse:v2.1.81"
    # system 应该是列表，且每项有 type 和 text
    assert isinstance(agent.system, list)
    for item in agent.system:
        assert "type" in item
        assert "text" in item


async def test_create_root_custom_prompt_version(tmp_path):
    session = _make_session(tmp_path)
    emitter = MagicMock()
    agent = AgentFactory.create_root(
        session, emitter, adapter=MagicMock(), prompt_version="simple_agent:v0.0.1"
    )
    assert isinstance(agent, Agent)
    assert agent.prompt_version == "simple_agent:v0.0.1"
