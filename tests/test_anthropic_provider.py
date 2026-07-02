"""tests/test_anthropic_provider.py — AnthropicProvider 完整链路测试。

验证 encode→call→decode 全链路：BaseLLMProvider.create 组合 AnthropicSDKAdapter + AnthropicCodec。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from ccserver.model_engine.providers.anthropic import AnthropicProvider
from ccserver.model_engine.adapters.anthropic_sdk import AnthropicSDKAdapter
from ccserver.model_engine.codecs.anthropic import AnthropicCodec
from ccserver.messages import UnifiedMessage, UnifiedTextBlock, UnifiedResponse


def _make_sdk_response(text="answer", stop_reason="end_turn"):
    """构造模拟的 Anthropic SDK Message 对象。"""
    msg = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = text
    msg.content = [block]
    msg.stop_reason = stop_reason
    usage = MagicMock()
    usage.input_tokens = 10
    usage.output_tokens = 5
    usage.cache_read_input_tokens = 0
    usage.cache_creation_input_tokens = 0
    msg.usage = usage
    return msg


def _make_mock_client(sdk_response=None):
    """构造模拟的 AsyncAnthropic 客户端。"""
    client = MagicMock()
    if sdk_response is None:
        sdk_response = _make_sdk_response()
    client.messages.create = AsyncMock(return_value=sdk_response)
    return client


@pytest.mark.asyncio
async def test_create_returns_unified_response():
    """create() 返回 UnifiedResponse，content 和 stop_reason 正确。"""
    client = _make_mock_client()
    provider = AnthropicProvider.from_client(client)
    msgs = [UnifiedMessage(role="user", content=[UnifiedTextBlock(text="hi")])]
    result = await provider.create(model="claude-3", messages=msgs, max_tokens=100)
    assert isinstance(result, UnifiedResponse)
    assert result.content == "answer"
    assert result.stop_reason == "end_turn"
    assert result.usage.input_tokens == 10


@pytest.mark.asyncio
async def test_create_encodes_system():
    """create() 将 system 参数正确编码传递给 SDK。"""
    client = _make_mock_client()
    provider = AnthropicProvider.from_client(client)
    msgs = [UnifiedMessage(role="user", content=[UnifiedTextBlock(text="hello")])]
    await provider.create(model="claude-3", messages=msgs, max_tokens=100, system="You are helpful")
    call_kw = client.messages.create.call_args.kwargs
    assert call_kw.get("system") == "You are helpful"


@pytest.mark.asyncio
async def test_create_encodes_tools():
    """create() 将 tools 参数正确编码传递给 SDK。"""
    client = _make_mock_client()
    provider = AnthropicProvider.from_client(client)
    msgs = [UnifiedMessage(role="user", content=[UnifiedTextBlock(text="use bash")])]
    tools = [{"name": "Bash", "description": "shell", "input_schema": {"type": "object", "properties": {}}}]
    await provider.create(model="claude-3", messages=msgs, max_tokens=100, tools=tools)
    call_kw = client.messages.create.call_args.kwargs
    assert "tools" in call_kw
    assert call_kw["tools"][0]["name"] == "Bash"


@pytest.mark.asyncio
async def test_create_passes_model_and_max_tokens():
    """create() 将 model 和 max_tokens 正确传递给 SDK。"""
    client = _make_mock_client()
    provider = AnthropicProvider.from_client(client)
    msgs = [UnifiedMessage(role="user", content=[UnifiedTextBlock(text="hi")])]
    await provider.create(model="claude-opus-4-8", messages=msgs, max_tokens=4096)
    call_kw = client.messages.create.call_args.kwargs
    assert call_kw["model"] == "claude-opus-4-8"
    assert call_kw["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_create_tool_use_response():
    """模型返回 tool_use 时，UnifiedResponse.tool_calls 有内容。"""
    sdk_msg = MagicMock()
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = "t1"
    tool_block.name = "Bash"
    tool_block.input = {"cmd": "ls"}
    sdk_msg.content = [tool_block]
    sdk_msg.stop_reason = "tool_use"
    usage = MagicMock()
    usage.input_tokens = 5
    usage.output_tokens = 3
    usage.cache_read_input_tokens = 0
    usage.cache_creation_input_tokens = 0
    sdk_msg.usage = usage

    client = _make_mock_client(sdk_response=sdk_msg)
    provider = AnthropicProvider.from_client(client)
    msgs = [UnifiedMessage(role="user", content=[UnifiedTextBlock(text="run ls")])]
    result = await provider.create(model="claude-3", messages=msgs, max_tokens=100)
    assert result.stop_reason == "tool_use"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "Bash"
    assert result.tool_calls[0].input == {"cmd": "ls"}
