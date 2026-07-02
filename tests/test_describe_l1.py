"""tests/test_describe_l1.py — describe_image_with_model 迁 L1。"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from ccserver.builtins.tools import vision as describe_mod


@pytest.mark.asyncio
async def test_describe_uses_llm_caller(monkeypatch):
    adapter = MagicMock()

    captured = {}

    async def fake_invoke(self, messages, **kw):
        captured["system"] = kw.get("system")
        resp = MagicMock()
        b = MagicMock(); b.type = "text"; b.text = "图片描述"
        resp.content = [b]
        return resp

    monkeypatch.setattr(describe_mod.LLMCaller, "invoke", fake_invoke)

    result = await describe_mod._describe_with_adapter(
        image_base64="ZmFrZQ==",
        prompt="描述这张图",
        adapter=adapter,
        model="vlm",
        max_tokens=512,
        system="你是视觉助手",
    )
    assert "图片描述" in result
    assert captured["system"] == "你是视觉助手"


@pytest.mark.asyncio
async def test_describe_joins_multiple_text_blocks(monkeypatch):
    """多个 TextBlock 应被拼接（保持迁移前行为）。"""
    adapter = MagicMock()

    def _tb(t):
        b = MagicMock(); b.type = "text"; b.text = t
        return b

    async def fake_invoke(self, messages, **kw):
        resp = MagicMock()
        resp.content = [_tb("第一段"), _tb("第二段")]
        return resp

    monkeypatch.setattr(describe_mod.LLMCaller, "invoke", fake_invoke)

    result = await describe_mod._describe_with_adapter(
        image_base64="ZmFrZQ==", prompt="p", adapter=adapter, model="vlm", max_tokens=512,
    )
    assert result == "第一段第二段"
