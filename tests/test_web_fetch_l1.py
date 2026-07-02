"""
tests/test_web_fetch_l1.py — web_fetch 的 LLM 理解迁 L1。

验证 _apply_prompt 已切换到 LLMCaller.invoke_text（获重试能力）。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

import ccserver.builtins.tools.web.web_fetch as wf


@pytest.mark.asyncio
async def test_web_fetch_llm_uses_invoke_text(monkeypatch):
    """_apply_prompt 调用 LLMCaller.invoke_text 并返回其结果。"""
    adapter = MagicMock()

    async def fake_invoke_text(self, messages, **kw):
        return "页面要点"

    monkeypatch.setattr(wf.LLMCaller, "invoke_text", fake_invoke_text)

    result = await wf._apply_prompt(
        adapter=adapter, model="m", url="http://x", content="一些网页内容", prompt="总结"
    )
    assert result == "页面要点"


@pytest.mark.asyncio
async def test_web_fetch_llm_returns_none_fallback(monkeypatch):
    """invoke_text 返回 None 时，_apply_prompt 返回固定 fallback 字符串。"""
    adapter = MagicMock()

    async def fake_invoke_text_none(self, messages, **kw):
        return None

    monkeypatch.setattr(wf.LLMCaller, "invoke_text", fake_invoke_text_none)

    result = await wf._apply_prompt(
        adapter=adapter, model="m", url="http://x", content="内容", prompt="总结"
    )
    assert result == "No response from model."


@pytest.mark.asyncio
async def test_web_fetch_llm_error_string(monkeypatch):
    """invoke_text 抛出异常时，_apply_prompt 返回 'LLM call failed: ...' 字符串。"""
    adapter = MagicMock()

    async def fake_invoke_text_error(self, messages, **kw):
        raise RuntimeError("network timeout")

    monkeypatch.setattr(wf.LLMCaller, "invoke_text", fake_invoke_text_error)

    result = await wf._apply_prompt(
        adapter=adapter, model="m", url="http://x", content="内容", prompt="总结"
    )
    assert result == "LLM call failed: network timeout"
