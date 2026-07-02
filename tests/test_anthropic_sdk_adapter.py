"""tests/test_anthropic_sdk_adapter.py — AnthropicSDKAdapter 测试。

验证：call/stream 正确透传参数给 SDK client，返回值是 raw SDK 对象（不做格式转换）。
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from ccserver.model_engine.adapters.anthropic_sdk import AnthropicSDKAdapter


def _make_mock_client():
    client = MagicMock()
    sdk_msg = MagicMock()
    sdk_msg.content = []
    sdk_msg.stop_reason = "end_turn"
    usage = MagicMock()
    usage.input_tokens = 0
    usage.output_tokens = 0
    sdk_msg.usage = usage
    client.messages.create = AsyncMock(return_value=sdk_msg)
    client.messages.stream = MagicMock(return_value=MagicMock())
    return client


@pytest.mark.asyncio
async def test_call_passes_model():
    client = _make_mock_client()
    adapter = AnthropicSDKAdapter(client=client)
    await adapter.call(model="claude-3", messages=[], max_tokens=100)
    kw = client.messages.create.call_args.kwargs
    assert kw["model"] == "claude-3"


@pytest.mark.asyncio
async def test_call_passes_messages():
    client = _make_mock_client()
    adapter = AnthropicSDKAdapter(client=client)
    msgs = [{"role": "user", "content": "hi"}]
    await adapter.call(model="claude-3", messages=msgs, max_tokens=50)
    kw = client.messages.create.call_args.kwargs
    assert kw["messages"] == msgs


@pytest.mark.asyncio
async def test_call_returns_raw_sdk_object():
    """返回值是 raw SDK 对象，不做格式转换。"""
    client = _make_mock_client()
    adapter = AnthropicSDKAdapter(client=client)
    result = await adapter.call(model="c", messages=[], max_tokens=10)
    # 返回值就是 mock 的 SDK 响应对象（不是 UnifiedResponse）
    assert result is client.messages.create.return_value


@pytest.mark.asyncio
async def test_call_passes_extra_kwargs():
    client = _make_mock_client()
    adapter = AnthropicSDKAdapter(client=client)
    await adapter.call(model="c", messages=[], max_tokens=10, system="You are helpful")
    kw = client.messages.create.call_args.kwargs
    assert kw["system"] == "You are helpful"


def test_stream_returns_sdk_context_manager():
    """stream 返回 SDK 原生 context manager。"""
    client = _make_mock_client()
    adapter = AnthropicSDKAdapter(client=client)
    result = adapter.stream(model="c", messages=[], max_tokens=10)
    # stream 被调用，返回值来自 SDK
    client.messages.stream.assert_called_once()
    assert result is client.messages.stream.return_value


def test_adapter_is_protocol_adapter_subclass():
    from ccserver.model_engine.adapters.base import ProtocolAdapter
    assert issubclass(AnthropicSDKAdapter, ProtocolAdapter)
