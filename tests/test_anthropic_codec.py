"""tests/test_anthropic_codec.py — AnthropicCodec 单元测试。

TDD RED → GREEN 顺序：
  1. 运行此文件，所有用例 FAIL（ImportError 或 AttributeError）。
  2. 实现 codecs/base.py + codecs/anthropic.py 后全部 PASS。

测试覆盖：
  - encode_messages（带/不带 system）
  - encode_tools 透传
  - encode_thinking（enabled/disabled）
  - decode_response（纯文本、思考链+文本、工具调用、未知块跳过、usage+cache）
  - decode_stream_chunk（text_delta、thinking_delta、非 content_block_delta → None）
"""

import pytest
from unittest.mock import MagicMock

from ccserver.model_engine.codecs.anthropic import AnthropicCodec
from ccserver.messages import (
    UnifiedMessage,
    UnifiedTextBlock,
    UnifiedThinkingBlock,
    UnifiedToolUseBlock,
    UnifiedToolResultBlock,
    UnifiedImageBlock,
    UnifiedImageThumbnailBlock,
    UnifiedCommandBlock,
    UnifiedStreamDelta,
    StreamState,
    UnifiedUsage,
    ThinkingConfig,
    UnifiedToolCall,
)


# ─────────────────────────────────────────────────────────────────────────────
# 辅助工厂
# ─────────────────────────────────────────────────────────────────────────────

def _make_codec() -> AnthropicCodec:
    """创建 AnthropicCodec 实例（无依赖，纯函数）。"""
    return AnthropicCodec()


def _make_user_msg(*blocks) -> UnifiedMessage:
    """构造 user UnifiedMessage，blocks 为可变参数。"""
    return UnifiedMessage(role="user", content=list(blocks))


def _make_assistant_msg(*blocks) -> UnifiedMessage:
    """构造 assistant UnifiedMessage，blocks 为可变参数。"""
    return UnifiedMessage(role="assistant", content=list(blocks))


# ─────────────────────────────────────────────────────────────────────────────
# encode_messages
# ─────────────────────────────────────────────────────────────────────────────

def test_encode_messages_with_string_system():
    """encode_messages 传入 system str，结果 dict 含 "system" 键。"""
    codec = _make_codec()
    messages = [_make_user_msg(UnifiedTextBlock(text="hello"))]
    result = codec.encode_messages(messages, system="You are helpful.")

    assert "system" in result
    assert result["system"] == "You are helpful."
    assert "messages" in result
    assert result["messages"][0]["role"] == "user"


def test_encode_messages_without_system():
    """encode_messages 不传 system，结果 dict 中无 "system" 键。"""
    codec = _make_codec()
    messages = [_make_user_msg(UnifiedTextBlock(text="hi"))]
    result = codec.encode_messages(messages)

    assert "system" not in result
    assert len(result["messages"]) == 1


def test_encode_messages_content_text_block():
    """TextBlock → {"type": "text", "text": ...}。"""
    codec = _make_codec()
    messages = [_make_user_msg(UnifiedTextBlock(text="world"))]
    result = codec.encode_messages(messages)

    content = result["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "world"}


def test_encode_messages_thinking_block_with_signature():
    """ThinkingBlock（有 signature）→ {"type":"thinking","thinking":...,"signature":...}。"""
    codec = _make_codec()
    block = UnifiedThinkingBlock(thinking="think...", signature="sig123")
    messages = [_make_assistant_msg(block)]
    result = codec.encode_messages(messages)

    content = result["messages"][0]["content"]
    assert content[0] == {"type": "thinking", "thinking": "think...", "signature": "sig123"}


def test_encode_messages_thinking_block_no_signature():
    """ThinkingBlock（无 signature）→ 不含 signature 键。"""
    codec = _make_codec()
    block = UnifiedThinkingBlock(thinking="reasoning...")
    messages = [_make_assistant_msg(block)]
    result = codec.encode_messages(messages)

    content = result["messages"][0]["content"]
    assert "signature" not in content[0]
    assert content[0]["thinking"] == "reasoning..."


def test_encode_messages_tool_use_block():
    """UnifiedToolUseBlock → {"type":"tool_use","id":...,"name":...,"input":...}。"""
    codec = _make_codec()
    block = UnifiedToolUseBlock(id="call_1", name="search", input={"query": "foo"})
    messages = [_make_assistant_msg(block)]
    result = codec.encode_messages(messages)

    content = result["messages"][0]["content"]
    assert content[0] == {
        "type": "tool_use",
        "id": "call_1",
        "name": "search",
        "input": {"query": "foo"},
    }


def test_encode_messages_tool_result_block():
    """UnifiedToolResultBlock → {"type":"tool_result","tool_use_id":...,"content":...,"is_error":...}。"""
    codec = _make_codec()
    block = UnifiedToolResultBlock(tool_use_id="call_1", content="ok", is_error=False)
    messages = [_make_user_msg(block)]
    result = codec.encode_messages(messages)

    content = result["messages"][0]["content"]
    assert content[0] == {
        "type": "tool_result",
        "tool_use_id": "call_1",
        "content": "ok",
        "is_error": False,
    }


def test_encode_messages_image_thumbnail_skipped():
    """UnifiedImageThumbnailBlock 应被过滤掉（不发给 API）。"""
    codec = _make_codec()
    block_thumb = UnifiedImageThumbnailBlock(source={"type": "base64", "data": "..."})
    block_text = UnifiedTextBlock(text="hello")
    messages = [_make_user_msg(block_thumb, block_text)]
    result = codec.encode_messages(messages)

    content = result["messages"][0]["content"]
    # thumbnail 被过滤，只剩 text
    assert len(content) == 1
    assert content[0]["type"] == "text"


def test_encode_messages_command_block_skipped():
    """UnifiedCommandBlock 应被过滤掉（不发给 API）。"""
    codec = _make_codec()
    block_cmd = UnifiedCommandBlock(name="bash", args="ls", stdout="file.py", body="file.py")
    block_text = UnifiedTextBlock(text="result")
    messages = [_make_user_msg(block_cmd, block_text)]
    result = codec.encode_messages(messages)

    content = result["messages"][0]["content"]
    assert len(content) == 1
    assert content[0]["type"] == "text"


# ─────────────────────────────────────────────────────────────────────────────
# encode_tools
# ─────────────────────────────────────────────────────────────────────────────

def test_encode_tools_passthrough():
    """Anthropic tools 格式 = 通用格式，透传。"""
    codec = _make_codec()
    tools = [{"name": "search", "description": "search web", "input_schema": {"type": "object"}}]
    result = codec.encode_tools(tools)

    assert result == {"tools": tools}


def test_encode_tools_none_returns_empty_dict():
    """None → {} （不注入 tools 键）。"""
    codec = _make_codec()
    result = codec.encode_tools(None)
    assert result == {}


def test_encode_tools_empty_list_returns_empty_dict():
    """空列表 → {}。"""
    codec = _make_codec()
    result = codec.encode_tools([])
    assert result == {}


# ─────────────────────────────────────────────────────────────────────────────
# encode_thinking
# ─────────────────────────────────────────────────────────────────────────────

def test_encode_thinking_enabled():
    """ThinkingConfig(enabled=True) → {"thinking": {"type": "adaptive", ...}}。"""
    codec = _make_codec()
    config = ThinkingConfig(enabled=True, effort="high")
    result = codec.encode_thinking(config)

    assert "thinking" in result
    assert result["thinking"]["type"] == "adaptive"


def test_encode_thinking_disabled():
    """ThinkingConfig(enabled=False) → {"thinking": {"type": "disabled"}}。"""
    codec = _make_codec()
    config = ThinkingConfig(enabled=False)
    result = codec.encode_thinking(config)

    assert result == {"thinking": {"type": "disabled"}}


# ─────────────────────────────────────────────────────────────────────────────
# decode_response
# ─────────────────────────────────────────────────────────────────────────────

def _make_sdk_text_block(text: str):
    """构造模拟 SDK text block。"""
    b = MagicMock()
    b.type = "text"
    b.text = text
    return b


def _make_sdk_thinking_block(thinking: str, signature: str = "sig"):
    """构造模拟 SDK thinking block。"""
    b = MagicMock()
    b.type = "thinking"
    b.thinking = thinking
    b.signature = signature
    return b


def _make_sdk_tool_use_block(id: str, name: str, input: dict):
    """构造模拟 SDK tool_use block。"""
    b = MagicMock()
    b.type = "tool_use"
    b.id = id
    b.name = name
    b.input = input
    return b


def _make_sdk_response(blocks: list, stop_reason: str = "end_turn", usage=None):
    """构造模拟 SDK Message 对象。"""
    resp = MagicMock()
    resp.content = blocks
    resp.stop_reason = stop_reason
    if usage is None:
        u = MagicMock()
        u.input_tokens = 10
        u.output_tokens = 5
        u.cache_read_input_tokens = 0
        u.cache_creation_input_tokens = 0
        resp.usage = u
    else:
        resp.usage = usage
    return resp


def test_decode_response_text_only():
    """纯文本响应 → content = 文本内容。"""
    codec = _make_codec()
    sdk_resp = _make_sdk_response([_make_sdk_text_block("hello world")])
    result = codec.decode_response(sdk_resp)

    assert result.content == "hello world"
    assert result.thinking == ""
    assert result.tool_calls == []


def test_decode_response_thinking_and_text():
    """thinking + text 响应 → thinking 和 content 分别设置。"""
    codec = _make_codec()
    sdk_resp = _make_sdk_response([
        _make_sdk_thinking_block("I think..."),
        _make_sdk_text_block("The answer is 42"),
    ])
    result = codec.decode_response(sdk_resp)

    assert result.thinking == "I think..."
    assert result.content == "The answer is 42"


def test_decode_response_tool_use():
    """tool_use 块 → tool_calls 列表包含 UnifiedToolCall。"""
    codec = _make_codec()
    sdk_resp = _make_sdk_response([
        _make_sdk_tool_use_block("call_abc", "search", {"query": "python"}),
    ])
    result = codec.decode_response(sdk_resp)

    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert isinstance(tc, UnifiedToolCall)
    assert tc.id == "call_abc"
    assert tc.name == "search"
    assert tc.input == {"query": "python"}


def test_decode_response_unknown_block_skipped():
    """未知 block type 仅记 warning，不影响解码结果（不抛异常）。"""
    codec = _make_codec()
    unknown_block = MagicMock()
    unknown_block.type = "redacted_thinking"
    sdk_resp = _make_sdk_response([unknown_block, _make_sdk_text_block("hi")])

    # 不应抛出任何异常
    result = codec.decode_response(sdk_resp)
    assert result.content == "hi"


def test_decode_response_stop_reason():
    """stop_reason 正确传递到 UnifiedResponse.stop_reason。"""
    codec = _make_codec()
    sdk_resp = _make_sdk_response([_make_sdk_text_block("x")], stop_reason="tool_use")
    result = codec.decode_response(sdk_resp)
    assert result.stop_reason == "tool_use"


def test_decode_response_usage_basic():
    """usage → UnifiedUsage（基本 input/output tokens）。"""
    codec = _make_codec()
    u = MagicMock()
    u.input_tokens = 100
    u.output_tokens = 50
    u.cache_read_input_tokens = 0
    u.cache_creation_input_tokens = 0
    sdk_resp = _make_sdk_response([_make_sdk_text_block("ok")], usage=u)
    result = codec.decode_response(sdk_resp)

    assert result.usage is not None
    assert isinstance(result.usage, UnifiedUsage)
    assert result.usage.input_tokens == 100
    assert result.usage.output_tokens == 50


def test_decode_response_usage_with_cache_fields():
    """usage → UnifiedUsage 含 cache_read_input_tokens + cache_creation_input_tokens。"""
    codec = _make_codec()
    u = MagicMock()
    u.input_tokens = 200
    u.output_tokens = 80
    u.cache_read_input_tokens = 50
    u.cache_creation_input_tokens = 30
    sdk_resp = _make_sdk_response([_make_sdk_text_block("ok")], usage=u)
    result = codec.decode_response(sdk_resp)

    assert result.usage.cache_read_input_tokens == 50
    assert result.usage.cache_creation_input_tokens == 30
    assert result.usage.total_tokens == 280


# ─────────────────────────────────────────────────────────────────────────────
# decode_stream_chunk
# ─────────────────────────────────────────────────────────────────────────────

def _make_content_block_delta(delta_type: str, text: str):
    """构造 content_block_delta 类型的 chunk。"""
    chunk = MagicMock()
    chunk.type = "content_block_delta"
    delta = MagicMock()
    delta.type = delta_type
    if delta_type == "text_delta":
        delta.text = text
    elif delta_type == "thinking_delta":
        delta.thinking = text
    chunk.delta = delta
    return chunk


def test_decode_stream_chunk_text_delta():
    """text_delta → UnifiedStreamDelta(kind='text', text=...) + 累积到 state.text_chunks。"""
    codec = _make_codec()
    state = StreamState()
    chunk = _make_content_block_delta("text_delta", "hello")
    result = codec.decode_stream_chunk(chunk, state)

    assert result is not None
    assert isinstance(result, UnifiedStreamDelta)
    assert result.kind == "text"
    assert result.text == "hello"
    assert state.text_chunks == ["hello"]


def test_decode_stream_chunk_thinking_delta():
    """thinking_delta → UnifiedStreamDelta(kind='thinking', text=...) + 累积到 state.thinking_chunks。"""
    codec = _make_codec()
    state = StreamState()
    chunk = _make_content_block_delta("thinking_delta", "I wonder...")
    result = codec.decode_stream_chunk(chunk, state)

    assert result is not None
    assert result.kind == "thinking"
    assert result.text == "I wonder..."
    assert state.thinking_chunks == ["I wonder..."]


def test_decode_stream_chunk_non_content_block_returns_none():
    """chunk.type != "content_block_delta" → 返回 None（不产出 delta）。"""
    codec = _make_codec()
    state = StreamState()
    chunk = MagicMock()
    chunk.type = "message_start"
    result = codec.decode_stream_chunk(chunk, state)

    assert result is None


def test_decode_stream_chunk_unknown_delta_type_returns_none():
    """content_block_delta 但 delta.type 未知 → 返回 None。"""
    codec = _make_codec()
    state = StreamState()
    chunk = MagicMock()
    chunk.type = "content_block_delta"
    delta = MagicMock()
    delta.type = "unknown_delta"
    chunk.delta = delta
    result = codec.decode_stream_chunk(chunk, state)

    assert result is None
