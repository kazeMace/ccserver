# tests/test_compactor_lib.py
import pytest
from unittest.mock import MagicMock, AsyncMock
from ccserver.compactor import Compactor
from ccserver.prompts_lib.cc_reverse.v2_1_81.lib import CcReverseV2181


def _make_adapter(summary_text: str) -> MagicMock:
    """构造一个模拟 ModelAdapter，adapter.create() 返回含指定摘要文本的响应。"""
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=summary_text)]
    adapter = MagicMock()
    adapter.create = AsyncMock(return_value=mock_response)
    return adapter


@pytest.mark.asyncio
async def test_compact_uses_lib_format():
    lib = CcReverseV2181()
    adapter = _make_adapter("summary content")

    session = MagicMock()
    session.id = "test-id-12345678"
    session.save_transcript = MagicMock(return_value="transcripts/test.json")

    emitter = MagicMock()
    emitter.emit_compact = AsyncMock()

    compactor = Compactor(adapter=adapter)
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    result = await compactor.compact(session, emitter, messages, lib=lib)

    assert len(result) == 2
    assert result[0]["role"] == "user"
    assert "summary content" in result[0]["content"]
    assert "transcripts/test.json" in result[0]["content"]
    assert result[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_compact_without_lib_uses_default_format():
    """lib=None 时保持原有行为（向后兼容）"""
    adapter = _make_adapter("fallback summary")

    session = MagicMock()
    session.id = "test-id-12345678"
    session.save_transcript = MagicMock(return_value="transcripts/fallback.json")

    emitter = MagicMock()
    emitter.emit_compact = AsyncMock()

    compactor = Compactor(adapter=adapter)
    result = await compactor.compact(session, emitter, [], lib=None)

    assert len(result) == 2
    assert "fallback summary" in result[0]["content"]
