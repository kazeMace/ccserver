"""
tests/test_bus_emitter_visibility.py — BusEmitter.set_visibility() 测试（P2-1）。

覆盖：
  - 构造时 visibility 为 FULL
  - set_visibility 切换后新 publish 的事件带新 visibility
  - 切换后不影响 agent_id / session_id 等其他字段
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from ccserver.emitters.bus_emitter import BusEmitter
from ccserver.event_bus import _VISIBILITY_FULL, _VISIBILITY_DONE_ONLY, _VISIBILITY_HIDDEN


class TestBusEmitterSetVisibility:

    def _make_emitter(self, visibility: str = _VISIBILITY_FULL) -> tuple[BusEmitter, list]:
        """辅助：构建 BusEmitter + 收集已 publish 事件。"""
        published = []

        bus = MagicMock()
        async def fake_publish(event):
            published.append(event)
        bus.publish = fake_publish

        emitter = BusEmitter(
            bus=bus,
            agent_id="agent-1",
            session_id="sess-1",
            visibility=visibility,
        )
        return emitter, published

    def test_default_visibility_is_full(self):
        """默认 visibility 应为 full。"""
        emitter, _ = self._make_emitter()
        assert emitter._visibility == _VISIBILITY_FULL

    def test_set_visibility_changes_visibility(self):
        """set_visibility 应更新 _visibility。"""
        emitter, _ = self._make_emitter()
        emitter.set_visibility(_VISIBILITY_DONE_ONLY)
        assert emitter._visibility == _VISIBILITY_DONE_ONLY

    @pytest.mark.anyio
    async def test_published_event_uses_current_visibility(self):
        """emit_token 发出的事件应带当前 visibility。"""
        emitter, published = self._make_emitter(_VISIBILITY_FULL)
        await emitter.emit_token("hello")
        assert len(published) == 1
        assert published[0].visibility == _VISIBILITY_FULL

    @pytest.mark.anyio
    async def test_after_set_visibility_event_uses_new_value(self):
        """set_visibility 后 emit_token 发出的事件带新 visibility。"""
        emitter, published = self._make_emitter(_VISIBILITY_FULL)
        emitter.set_visibility(_VISIBILITY_DONE_ONLY)
        await emitter.emit_token("hello")
        assert published[0].visibility == _VISIBILITY_DONE_ONLY

    @pytest.mark.anyio
    async def test_set_visibility_does_not_affect_agent_id(self):
        """set_visibility 不影响 agent_id 字段。"""
        emitter, published = self._make_emitter()
        emitter.set_visibility(_VISIBILITY_HIDDEN)
        await emitter.emit_done("result")
        assert published[0].agent_id == "agent-1"

    @pytest.mark.anyio
    async def test_set_visibility_multiple_times(self):
        """多次 set_visibility，最后一次生效。"""
        emitter, published = self._make_emitter()
        emitter.set_visibility(_VISIBILITY_DONE_ONLY)
        emitter.set_visibility(_VISIBILITY_HIDDEN)
        emitter.set_visibility(_VISIBILITY_FULL)
        await emitter.emit_token("x")
        assert published[0].visibility == _VISIBILITY_FULL
