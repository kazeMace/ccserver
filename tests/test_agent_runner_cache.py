"""
tests/test_agent_runner_cache.py — AgentRunner LRU Agent 缓存测试（P2-3）。

覆盖：
  - 首次 run：缓存未命中，新建 Agent 并存入缓存
  - 第二次 run 同一 session：命中缓存，复用 Agent
  - TTL 过期后：缓存失效，重新建 Agent
  - LRU 驱逐：超出 MAX_SIZE 时淘汰最久未使用的条目
  - invalidate_agent：主动让缓存失效
"""

import time
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from ccserver.main import AgentRunner, _AGENT_CACHE_MAX_SIZE, _AGENT_CACHE_TTL_S


class TestAgentRunnerCacheOperations:

    def test_cache_miss_returns_none(self):
        runner = AgentRunner()
        assert runner._cache_get("nonexistent") is None

    def test_cache_put_and_get_hit(self):
        runner = AgentRunner()
        fake_agent = MagicMock()
        runner._cache_put("sess-1", fake_agent)
        result = runner._cache_get("sess-1")
        assert result is fake_agent

    def test_cache_get_updates_lru_order(self):
        runner = AgentRunner()
        a1, a2 = MagicMock(), MagicMock()
        runner._cache_put("s1", a1)
        runner._cache_put("s2", a2)
        runner._cache_get("s1")
        keys = list(runner._agent_cache.keys())
        assert keys[-1] == "s1"

    def test_cache_ttl_expiry_returns_none(self):
        runner = AgentRunner()
        fake_agent = MagicMock()
        runner._agent_cache["sess-x"] = (fake_agent, time.monotonic() - _AGENT_CACHE_TTL_S - 1)
        result = runner._cache_get("sess-x")
        assert result is None
        assert "sess-x" not in runner._agent_cache

    def test_cache_max_size_evicts_lru(self):
        runner = AgentRunner()
        for i in range(_AGENT_CACHE_MAX_SIZE):
            runner._cache_put(f"sess-{i}", MagicMock())
        assert len(runner._agent_cache) == _AGENT_CACHE_MAX_SIZE
        runner._cache_put("sess-new", MagicMock())
        assert len(runner._agent_cache) == _AGENT_CACHE_MAX_SIZE
        assert "sess-0" not in runner._agent_cache
        assert "sess-new" in runner._agent_cache

    def test_invalidate_agent_removes_from_cache(self):
        runner = AgentRunner()
        runner._cache_put("sess-1", MagicMock())
        existed = runner.invalidate_agent("sess-1")
        assert existed is True
        assert runner._cache_get("sess-1") is None

    def test_invalidate_agent_nonexistent_returns_false(self):
        runner = AgentRunner()
        existed = runner.invalidate_agent("nonexistent")
        assert existed is False


class TestAgentRunnerCacheIntegration:

    def _make_fake_agent(self, agent_id="agent-abc"):
        agent = MagicMock()
        agent.run = AsyncMock(return_value="done")
        agent.context = MagicMock()
        agent.context.agent_id = agent_id
        agent.context.name = "orchestrator"
        agent.emitter = MagicMock()
        return agent

    def _make_session(self, session_id="test-session-id"):
        session = MagicMock()
        session.id = session_id
        session.mcp = None
        session.workdir = MagicMock()
        session.project_root = MagicMock()
        session.hooks.emit_void = AsyncMock(return_value=None)
        session.config.model.to_model_endpoint = MagicMock(return_value=MagicMock())
        session.config.model.model_id = "claude-test"
        return session

    @pytest.mark.anyio
    async def test_first_run_creates_agent_and_caches(self):
        runner = AgentRunner()
        session = self._make_session()
        fake_agent = self._make_fake_agent()
        emitter = MagicMock()

        with patch("ccserver.main.AgentFactory.create_root", return_value=fake_agent) as mock_create, \
             patch("ccserver.main.AdapterFactory.build", return_value=MagicMock()):
            await runner.run(session, "hello", emitter)

        mock_create.assert_called_once()
        assert runner._cache_get(session.id) is fake_agent

    @pytest.mark.anyio
    async def test_second_run_hits_cache(self):
        runner = AgentRunner()
        session = self._make_session()
        fake_agent = self._make_fake_agent()
        emitter = MagicMock()

        with patch("ccserver.main.AgentFactory.create_root", return_value=fake_agent) as mock_create, \
             patch("ccserver.main.AdapterFactory.build", return_value=MagicMock()):
            await runner.run(session, "hello", emitter)
            await runner.run(session, "world", emitter)

        assert mock_create.call_count == 1

    @pytest.mark.anyio
    async def test_invalidate_forces_new_agent_creation(self):
        runner = AgentRunner()
        session = self._make_session()
        fake_agent = self._make_fake_agent()
        emitter = MagicMock()

        with patch("ccserver.main.AgentFactory.create_root", return_value=fake_agent) as mock_create, \
             patch("ccserver.main.AdapterFactory.build", return_value=MagicMock()):
            await runner.run(session, "hello", emitter)
            runner.invalidate_agent(session.id)
            await runner.run(session, "new topic", emitter)

        assert mock_create.call_count == 2

    @pytest.mark.anyio
    async def test_different_sessions_each_get_own_agent(self):
        runner = AgentRunner()
        agent_a = self._make_fake_agent("agent-a")
        agent_b = self._make_fake_agent("agent-b")
        session_a = self._make_session("session-a")
        session_b = self._make_session("session-b")
        emitter = MagicMock()

        with patch("ccserver.main.AgentFactory.create_root", side_effect=[agent_a, agent_b]) as mock_create, \
             patch("ccserver.main.AdapterFactory.build", return_value=MagicMock()):
            await runner.run(session_a, "hello a", emitter)
            await runner.run(session_b, "hello b", emitter)

        assert mock_create.call_count == 2
        assert runner._cache_get("session-a") is agent_a
        assert runner._cache_get("session-b") is agent_b
