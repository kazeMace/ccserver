"""tests/test_chat_completions_adapter.py — ChatCompletionsAdapter 测试。

验证：call/stream 正确透传参数给 SDK client，返回值是 raw SDK 对象。

Mock 说明：
- call() 使用 await，因此 client.chat.completions.create 用 AsyncMock
- stream() 是同步调用（返回 async context manager），因此用普通 MagicMock
  两类测试分别用各自的 helper，避免 AsyncMock 同步调用返回 coroutine 的干扰
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from ccserver.model_engine.adapters.chat_completions import ChatCompletionsAdapter
from ccserver.model_engine.adapters.base import ProtocolAdapter


def _make_mock_client_for_call():
    """
    构造供 call()（非流式 await 调用）测试用的 mock client。

    create 使用 AsyncMock，await 时返回 resp。
    """
    client = MagicMock()
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message = MagicMock(content="hello", tool_calls=None)
    resp.choices[0].finish_reason = "stop"
    resp.usage = MagicMock(prompt_tokens=5, completion_tokens=2, total_tokens=7)
    client.chat.completions.create = AsyncMock(return_value=resp)
    return client


def _make_mock_client_for_stream():
    """
    构造供 stream()（同步调用，返回 async context manager）测试用的 mock client。

    create 使用普通 MagicMock，同步调用直接返回 return_value（mock stream 对象）。
    """
    client = MagicMock()
    stream_ctx = MagicMock()  # 模拟 async stream context manager
    client.chat.completions.create = MagicMock(return_value=stream_ctx)
    return client


@pytest.mark.asyncio
async def test_call_passes_model_and_messages():
    client = _make_mock_client_for_call()
    adapter = ChatCompletionsAdapter(client=client)
    msgs = [{"role": "user", "content": "hi"}]
    await adapter.call(model="gpt-4o", messages=msgs, max_tokens=100)
    kw = client.chat.completions.create.call_args.kwargs
    assert kw["model"] == "gpt-4o"
    assert kw["messages"] == msgs
    assert kw["max_tokens"] == 100


@pytest.mark.asyncio
async def test_call_removes_stream_param():
    """call() 应移除 stream 参数，确保非流式。"""
    client = _make_mock_client_for_call()
    adapter = ChatCompletionsAdapter(client=client)
    await adapter.call(model="gpt-4o", messages=[], max_tokens=10, stream=True)
    kw = client.chat.completions.create.call_args.kwargs
    assert "stream" not in kw


@pytest.mark.asyncio
async def test_call_returns_raw_sdk_object():
    """call() 返回值是 raw SDK ChatCompletion 对象（不做格式转换）。"""
    client = _make_mock_client_for_call()
    adapter = ChatCompletionsAdapter(client=client)
    result = await adapter.call(model="gpt-4o", messages=[], max_tokens=10)
    # AsyncMock await 后返回 return_value（即 resp）
    assert result is client.chat.completions.create.return_value


def test_stream_adds_stream_true():
    """stream() 应在 params 中加入 stream=True。"""
    client = _make_mock_client_for_stream()
    adapter = ChatCompletionsAdapter(client=client)
    adapter.stream(model="gpt-4o", messages=[], max_tokens=10)
    kw = client.chat.completions.create.call_args.kwargs
    assert kw["stream"] is True


def test_stream_returns_sdk_result():
    """stream() 返回 SDK 原生 stream 对象（不包装）。"""
    client = _make_mock_client_for_stream()
    adapter = ChatCompletionsAdapter(client=client)
    result = adapter.stream(model="gpt-4o", messages=[], max_tokens=10)
    # 同步 MagicMock 调用直接返回 return_value
    assert result is client.chat.completions.create.return_value


def test_is_protocol_adapter_subclass():
    assert issubclass(ChatCompletionsAdapter, ProtocolAdapter)
