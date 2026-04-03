"""
tests/test_anthropic_adapter.py — AnthropicAdapter 单元测试

覆盖：
  - create() 将参数正确透传给 client.messages.create
  - create() system=None 时不传 system 参数
  - create() system 有值时传入
  - create() tools=None 时不传 tools 参数
  - create() tools 有值时传入
  - create() 额外 kwargs 透传
  - stream() 将参数正确透传给 client.messages.stream（同步上下文管理器）
  - stream() system/tools 可选透传
  - get_default_adapter() 返回 AnthropicAdapter 实例（进程级单例）
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from ccserver.model.anthropic_adapter import AnthropicAdapter


# ─── 辅助 ─────────────────────────────────────────────────────────────────────


def _make_adapter():
    """创建一个持有 mock client 的 AnthropicAdapter。"""
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=MagicMock(stop_reason="end_turn"))
    mock_client.messages.stream = MagicMock(return_value=MagicMock())  # 同步 context manager
    return AnthropicAdapter(client=mock_client), mock_client


# ─── create() ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_passes_required_params():
    adapter, client = _make_adapter()
    await adapter.create(model="claude-3", messages=[{"role": "user", "content": "hi"}], max_tokens=100)
    client.messages.create.assert_called_once()
    call_kwargs = client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-3"
    assert call_kwargs["messages"] == [{"role": "user", "content": "hi"}]
    assert call_kwargs["max_tokens"] == 100


@pytest.mark.asyncio
async def test_create_system_none_not_passed():
    adapter, client = _make_adapter()
    await adapter.create(model="m", messages=[], max_tokens=10, system=None)
    call_kwargs = client.messages.create.call_args.kwargs
    assert "system" not in call_kwargs


@pytest.mark.asyncio
async def test_create_system_passed_when_given():
    adapter, client = _make_adapter()
    system = [{"type": "text", "text": "You are helpful."}]
    await adapter.create(model="m", messages=[], max_tokens=10, system=system)
    call_kwargs = client.messages.create.call_args.kwargs
    assert call_kwargs["system"] == system


@pytest.mark.asyncio
async def test_create_system_as_string():
    adapter, client = _make_adapter()
    await adapter.create(model="m", messages=[], max_tokens=10, system="Be helpful.")
    call_kwargs = client.messages.create.call_args.kwargs
    assert call_kwargs["system"] == "Be helpful."


@pytest.mark.asyncio
async def test_create_tools_none_not_passed():
    adapter, client = _make_adapter()
    await adapter.create(model="m", messages=[], max_tokens=10, tools=None)
    call_kwargs = client.messages.create.call_args.kwargs
    assert "tools" not in call_kwargs


@pytest.mark.asyncio
async def test_create_tools_passed_when_given():
    adapter, client = _make_adapter()
    tools = [{"name": "Bash", "description": "Run bash"}]
    await adapter.create(model="m", messages=[], max_tokens=10, tools=tools)
    call_kwargs = client.messages.create.call_args.kwargs
    assert call_kwargs["tools"] == tools


@pytest.mark.asyncio
async def test_create_extra_kwargs_forwarded():
    adapter, client = _make_adapter()
    await adapter.create(model="m", messages=[], max_tokens=10, temperature=0.5)
    call_kwargs = client.messages.create.call_args.kwargs
    assert call_kwargs["temperature"] == 0.5


@pytest.mark.asyncio
async def test_create_returns_client_response():
    adapter, client = _make_adapter()
    mock_response = MagicMock(stop_reason="end_turn")
    client.messages.create = AsyncMock(return_value=mock_response)
    result = await adapter.create(model="m", messages=[], max_tokens=10)
    assert result is mock_response


# ─── stream() ────────────────────────────────────────────────────────────────


def test_stream_passes_required_params():
    adapter, client = _make_adapter()
    adapter.stream(model="claude-3", messages=[{"role": "user", "content": "stream me"}], max_tokens=200)
    client.messages.stream.assert_called_once()
    call_kwargs = client.messages.stream.call_args.kwargs
    assert call_kwargs["model"] == "claude-3"
    assert call_kwargs["max_tokens"] == 200


def test_stream_system_none_not_passed():
    adapter, client = _make_adapter()
    adapter.stream(model="m", messages=[], max_tokens=10, system=None)
    call_kwargs = client.messages.stream.call_args.kwargs
    assert "system" not in call_kwargs


def test_stream_system_passed_when_given():
    adapter, client = _make_adapter()
    adapter.stream(model="m", messages=[], max_tokens=10, system="sys prompt")
    call_kwargs = client.messages.stream.call_args.kwargs
    assert call_kwargs["system"] == "sys prompt"


def test_stream_tools_none_not_passed():
    adapter, client = _make_adapter()
    adapter.stream(model="m", messages=[], max_tokens=10, tools=None)
    call_kwargs = client.messages.stream.call_args.kwargs
    assert "tools" not in call_kwargs


def test_stream_tools_passed_when_given():
    adapter, client = _make_adapter()
    tools = [{"name": "Read", "description": "Read file"}]
    adapter.stream(model="m", messages=[], max_tokens=10, tools=tools)
    call_kwargs = client.messages.stream.call_args.kwargs
    assert call_kwargs["tools"] == tools


def test_stream_returns_context_manager():
    adapter, client = _make_adapter()
    mock_ctx = MagicMock()
    client.messages.stream.return_value = mock_ctx
    result = adapter.stream(model="m", messages=[], max_tokens=10)
    assert result is mock_ctx


# ─── get_default_adapter() ───────────────────────────────────────────────────


def test_get_default_adapter_returns_anthropic_adapter():
    from ccserver.model.anthropic_adapter import get_default_adapter, _default_adapter
    import ccserver.model.anthropic_adapter as mod

    # 清除单例以确保测试独立
    original = mod._default_adapter
    mod._default_adapter = None

    try:
        adapter = get_default_adapter()
        assert isinstance(adapter, AnthropicAdapter)
    finally:
        mod._default_adapter = original


def test_get_default_adapter_is_singleton():
    from ccserver.model.anthropic_adapter import get_default_adapter
    import ccserver.model.anthropic_adapter as mod

    original = mod._default_adapter
    mod._default_adapter = None

    try:
        a1 = get_default_adapter()
        a2 = get_default_adapter()
        assert a1 is a2
    finally:
        mod._default_adapter = original
