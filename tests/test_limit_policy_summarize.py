"""tests/test_limit_policy_summarize.py — SummarizeStrategy 迁 L1 后行为。"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from ccserver.agent.limit_policy import SummarizeStrategy


def _text_block(text):
    b = MagicMock(); b.type = "text"; b.text = text
    return b


@pytest.mark.asyncio
async def test_summarize_uses_llm_caller_invoke_text(monkeypatch):
    """SummarizeStrategy 通过 L1 LLMCaller.invoke_text 拿摘要。"""
    rt = MagicMock()
    rt.adapter = MagicMock()
    rt.model = "m"
    rt.aid_label = "a(x)"
    rt.context = MagicMock()
    rt.context.messages = [{"role": "user", "content": "hello"}]
    rt.context.is_orchestrator = False
    rt.session = MagicMock()
    rt.session.hooks = MagicMock()
    rt.session.hooks.emit_void = AsyncMock()
    rt._build_hook_ctx = MagicMock(return_value=MagicMock())
    rt.emitter = MagicMock()
    rt.emitter.emit_subagent_done = AsyncMock()

    import ccserver.agent.limit_policy as mod

    async def fake_invoke_text(self, messages, **kw):
        return "这是摘要"

    monkeypatch.setattr(mod.LLMCaller, "invoke_text", fake_invoke_text)

    outcome = await SummarizeStrategy().handle(rt, last_text="原文")
    assert "这是摘要" in outcome.final_text
