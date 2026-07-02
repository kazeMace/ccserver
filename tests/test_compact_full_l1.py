"""tests/test_compact_full_l1.py — DefaultFullCompactor._summarize_with_llm 迁 L1。"""

import pytest
from unittest.mock import MagicMock

from ccserver.compact.full import DefaultFullCompactor


@pytest.mark.asyncio
async def test_summarize_with_llm_uses_invoke_and_forwards_thinking(monkeypatch):
    adapter = MagicMock()
    compactor = DefaultFullCompactor(adapter=adapter, model="m")

    import ccserver.compact.full as mod

    captured = {}

    def _text_block(t):
        b = MagicMock(); b.type = "text"; b.text = t
        return b

    async def fake_invoke(self, messages, **kw):
        captured["thinking"] = kw.get("thinking")
        resp = MagicMock()
        resp.content = [_text_block("压缩摘要")]
        return resp

    monkeypatch.setattr(mod.LLMCaller, "invoke", fake_invoke)

    result = await compactor._summarize_with_llm([{"role": "user", "content": "hi"}])
    assert result == "压缩摘要"
    assert captured["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_summarize_falls_back_to_thinking_block(monkeypatch):
    """无 TextBlock 但有 ThinkingBlock → 降级返回思考文本，不崩溃。"""
    adapter = MagicMock()
    compactor = DefaultFullCompactor(adapter=adapter, model="m")

    import ccserver.compact.full as mod

    def _thinking_block(t):
        b = MagicMock(); b.type = "thinking"; b.thinking = t
        return b

    async def fake_invoke(self, messages, **kw):
        resp = MagicMock()
        resp.content = [_thinking_block("思考链摘要")]
        return resp

    monkeypatch.setattr(mod.LLMCaller, "invoke", fake_invoke)

    result = await compactor._summarize_with_llm([{"role": "user", "content": "hi"}])
    assert result == "思考链摘要"
