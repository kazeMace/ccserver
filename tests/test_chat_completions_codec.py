"""tests/test_chat_completions_codec.py — ChatCompletionsCodec 单元测试。

TDD RED → GREEN 顺序：
  1. 运行此文件，所有用例 FAIL（ImportError 或 AttributeError）。
  2. 实现 codecs/chat_completions.py 后全部 PASS。

测试覆盖：
  - encode_messages（system 作为第一条、无 system、assistant 含 tool_calls、user tool_result）
  - encode_tools（function 格式转换、None → {}）
  - decode_response（文本、工具调用、reasoning_content）
  - finish_reason_hook 映射
  - _build_usage 基础字段
  - decode_stream_chunk（text delta、tool_call 累积）
"""

import json
import pytest
from unittest.mock import MagicMock

from ccserver.model_engine.codecs.chat_completions import ChatCompletionsCodec
from ccserver.messages import (
    UnifiedMessage,
    UnifiedTextBlock,
    UnifiedThinkingBlock,
    UnifiedToolUseBlock,
    UnifiedToolResultBlock,
    UnifiedImageThumbnailBlock,
    UnifiedCommandBlock,
    UnifiedStreamDelta,
    StreamState,
    UnifiedUsage,
    UnifiedToolCall,
)


# ─────────────────────────────────────────────────────────────────────────────
# 辅助工厂
# ─────────────────────────────────────────────────────────────────────────────

def _make_codec() -> ChatCompletionsCodec:
    """创建 ChatCompletionsCodec 实例（无依赖，纯函数）。"""
    return ChatCompletionsCodec()


def _make_user_msg(*blocks) -> UnifiedMessage:
    return UnifiedMessage(role="user", content=list(blocks))


def _make_assistant_msg(*blocks) -> UnifiedMessage:
    return UnifiedMessage(role="assistant", content=list(blocks))


# ─────────────────────────────────────────────────────────────────────────────
# encode_messages
# ─────────────────────────────────────────────────────────────────────────────

def test_encode_messages_system_as_first_message():
    """system str → 第一条 {"role": "system", "content": ...} 消息。"""
    codec = _make_codec()
    messages = [_make_user_msg(UnifiedTextBlock(text="hi"))]
    result = codec.encode_messages(messages, system="You are helpful.")

    assert result["messages"][0]["role"] == "system"
    assert result["messages"][0]["content"] == "You are helpful."
    # 保证 system 在最前面
    assert result["messages"][1]["role"] == "user"


def test_encode_messages_no_system():
    """无 system 时，第一条消息就是 user。"""
    codec = _make_codec()
    messages = [_make_user_msg(UnifiedTextBlock(text="hello"))]
    result = codec.encode_messages(messages)

    assert result["messages"][0]["role"] == "user"
    assert len(result["messages"]) == 1


def test_encode_messages_assistant_with_tool_calls():
    """assistant 消息含 UnifiedToolUseBlock → openai tool_calls 格式。"""
    codec = _make_codec()
    block = UnifiedToolUseBlock(id="call_1", name="search", input={"q": "foo"})
    messages = [_make_assistant_msg(block)]
    result = codec.encode_messages(messages)

    msg = result["messages"][0]
    assert msg["role"] == "assistant"
    assert "tool_calls" in msg
    assert len(msg["tool_calls"]) == 1
    tc = msg["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "search"
    # arguments 应为 JSON 字符串
    parsed = json.loads(tc["function"]["arguments"])
    assert parsed == {"q": "foo"}


def test_encode_messages_assistant_text_only():
    """assistant 纯文本消息 → content 为字符串。"""
    codec = _make_codec()
    messages = [_make_assistant_msg(UnifiedTextBlock(text="done"))]
    result = codec.encode_messages(messages)

    msg = result["messages"][0]
    assert msg["role"] == "assistant"
    assert msg["content"] == "done"
    assert "tool_calls" not in msg or msg.get("tool_calls") is None


def test_encode_messages_user_tool_result():
    """UnifiedToolResultBlock → {"role":"tool","tool_call_id":...,"content":...}。"""
    codec = _make_codec()
    block = UnifiedToolResultBlock(tool_use_id="call_1", content="search result", is_error=False)
    messages = [_make_user_msg(block)]
    result = codec.encode_messages(messages)

    # tool result 应该展开成独立的 tool role 消息
    tool_msgs = [m for m in result["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_1"
    assert tool_msgs[0]["content"] == "search result"


def test_encode_messages_image_thumbnail_skipped():
    """UnifiedImageThumbnailBlock 在 encode 时被过滤（不发给 API）。"""
    codec = _make_codec()
    thumb = UnifiedImageThumbnailBlock(source={"type": "base64", "data": "..."})
    text = UnifiedTextBlock(text="visible")
    messages = [_make_user_msg(thumb, text)]
    result = codec.encode_messages(messages)

    # 只剩 user 消息，内容只含 text
    user_msgs = [m for m in result["messages"] if m["role"] == "user"]
    assert len(user_msgs) == 1
    content = user_msgs[0]["content"]
    # 纯文本时 content 是 str，不含缩略图
    if isinstance(content, list):
        types = [b.get("type") for b in content if isinstance(b, dict)]
        assert "image_thumbnail" not in types
    else:
        assert isinstance(content, str)


def test_encode_messages_command_block_skipped():
    """UnifiedCommandBlock 在 encode 时被过滤（不发给 API）。"""
    codec = _make_codec()
    cmd = UnifiedCommandBlock(name="bash", args="ls", stdout="file.py", body="file.py")
    text = UnifiedTextBlock(text="answer")
    messages = [_make_user_msg(cmd, text)]
    result = codec.encode_messages(messages)

    user_msgs = [m for m in result["messages"] if m["role"] == "user"]
    assert len(user_msgs) == 1


# ─────────────────────────────────────────────────────────────────────────────
# encode_tools
# ─────────────────────────────────────────────────────────────────────────────

def test_encode_tools_function_format():
    """unified tool → openai {"type":"function","function":{name, description, parameters}}。"""
    codec = _make_codec()
    tools = [
        {
            "name": "search",
            "description": "search the web",
            "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
        }
    ]
    result = codec.encode_tools(tools)

    assert "tools" in result
    openai_tools = result["tools"]
    assert len(openai_tools) == 1
    t = openai_tools[0]
    assert t["type"] == "function"
    assert t["function"]["name"] == "search"
    assert t["function"]["description"] == "search the web"
    assert t["function"]["parameters"] == tools[0]["input_schema"]


def test_encode_tools_none_returns_empty_dict():
    """None → {} （不注入 tools 键）。"""
    codec = _make_codec()
    assert codec.encode_tools(None) == {}


def test_encode_tools_empty_list_returns_empty_dict():
    """空列表 → {}。"""
    codec = _make_codec()
    assert codec.encode_tools([]) == {}


# ─────────────────────────────────────────────────────────────────────────────
# decode_response
# ─────────────────────────────────────────────────────────────────────────────

def _make_openai_response(
    content: str = "",
    tool_calls=None,
    finish_reason: str = "stop",
    reasoning_content: str = None,
    reasoning: str = None,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    total_tokens: int = 15,
):
    """构造模拟 OpenAI ChatCompletion 响应对象。"""
    resp = MagicMock()

    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls

    # reasoning_content / reasoning 字段（deepseek-style）
    if reasoning_content is not None:
        message.reasoning_content = reasoning_content
    else:
        # 使 getattr 返回 None
        type(message).reasoning_content = property(lambda self: None)

    if reasoning is not None:
        message.reasoning = reasoning
    else:
        type(message).reasoning = property(lambda self: None)

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason

    resp.choices = [choice]

    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    usage.total_tokens = total_tokens
    resp.usage = usage

    return resp


def test_decode_response_text_content():
    """纯文本响应 → content = 文本内容，stop_reason 映射正确。"""
    codec = _make_codec()
    resp = _make_openai_response(content="Hello!")
    result = codec.decode_response(resp)

    assert result.content == "Hello!"
    assert result.stop_reason == "end_turn"


def test_decode_response_with_tool_calls():
    """含 tool_calls → tool_calls 列表包含 UnifiedToolCall。"""
    codec = _make_codec()

    tc = MagicMock()
    tc.id = "call_xyz"
    tc.function = MagicMock()
    tc.function.name = "calc"
    tc.function.arguments = '{"x": 1}'

    resp = _make_openai_response(content="", tool_calls=[tc], finish_reason="tool_calls")
    result = codec.decode_response(resp)

    assert len(result.tool_calls) == 1
    tool = result.tool_calls[0]
    assert isinstance(tool, UnifiedToolCall)
    assert tool.id == "call_xyz"
    assert tool.name == "calc"
    assert tool.input == {"x": 1}
    assert result.stop_reason == "tool_use"


def test_decode_response_reasoning_content():
    """deepseek-style reasoning_content → thinking 字段。"""
    codec = _make_codec()

    # 用 MagicMock 直接设置属性（避免 property 覆盖问题）
    resp = MagicMock()
    message = MagicMock()
    # 直接赋值属性，让 getattr 能取到
    message.configure_mock(**{
        "reasoning_content": "deep thought",
        "reasoning": None,
        "content": "answer",
        "tool_calls": None,
    })
    choice = MagicMock()
    choice.message = message
    choice.finish_reason = "stop"
    resp.choices = [choice]
    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 5
    usage.total_tokens = 15
    resp.usage = usage

    result = codec.decode_response(resp)
    assert result.thinking == "deep thought"
    assert result.content == "answer"


def test_finish_reason_hook_mapping():
    """finish_reason_hook 正确映射 stop/tool_calls/length。"""
    codec = _make_codec()
    assert codec.finish_reason_hook("stop") == "end_turn"
    assert codec.finish_reason_hook("tool_calls") == "tool_use"
    assert codec.finish_reason_hook("length") == "max_tokens"
    assert codec.finish_reason_hook(None) == "end_turn"
    assert codec.finish_reason_hook("unknown") == "end_turn"


def test_build_usage_basic():
    """prompt_tokens/completion_tokens/total_tokens → UnifiedUsage。"""
    codec = _make_codec()
    u = MagicMock()
    u.prompt_tokens = 100
    u.completion_tokens = 50
    u.total_tokens = 150
    result = codec._build_usage(u)

    assert isinstance(result, UnifiedUsage)
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.total_tokens == 150


def test_build_usage_none():
    """u = None → None。"""
    codec = _make_codec()
    assert codec._build_usage(None) is None


# ─────────────────────────────────────────────────────────────────────────────
# decode_stream_chunk
# ─────────────────────────────────────────────────────────────────────────────

def _make_openai_stream_chunk(
    content: str = None,
    reasoning_content: str = None,
    reasoning: str = None,
    tool_calls=None,
):
    """构造模拟 OpenAI 流式 chunk。"""
    chunk = MagicMock()
    delta = MagicMock()

    delta.content = content
    delta.tool_calls = tool_calls

    # reasoning 字段用 configure_mock 设置（避免 Mock 自动返回 Mock 对象）
    delta.configure_mock(**{
        "reasoning_content": reasoning_content,
        "reasoning": reasoning,
    })

    choice = MagicMock()
    choice.delta = delta
    chunk.choices = [choice]
    return chunk


def test_decode_stream_chunk_text_delta():
    """content 字段 → UnifiedStreamDelta(kind='text') + 累积 state.text_chunks。"""
    codec = _make_codec()
    state = StreamState()
    chunk = _make_openai_stream_chunk(content="hello")
    result = codec.decode_stream_chunk(chunk, state)

    assert result is not None
    assert result.kind == "text"
    assert result.text == "hello"
    assert state.text_chunks == ["hello"]


def test_decode_stream_chunk_reasoning_delta():
    """reasoning_content 字段 → UnifiedStreamDelta(kind='thinking') + 累积 state.thinking_chunks。"""
    codec = _make_codec()
    state = StreamState()
    chunk = MagicMock()
    delta = MagicMock()
    # 直接设置属性让 getattr 能取到
    delta.reasoning_content = "thinking..."
    delta.reasoning = None
    delta.content = None
    delta.tool_calls = None
    choice = MagicMock()
    choice.delta = delta
    chunk.choices = [choice]

    result = codec.decode_stream_chunk(chunk, state)

    assert result is not None
    assert result.kind == "thinking"
    assert result.text == "thinking..."
    assert state.thinking_chunks == ["thinking..."]


def test_decode_stream_chunk_tool_call_accumulation():
    """tool_calls delta → 累积到 state.tool_calls_raw，返回 None。"""
    codec = _make_codec()
    state = StreamState()

    # 模拟 tool_calls delta（第一个片段：id + name）
    tc_delta = MagicMock()
    tc_delta.index = 0
    tc_delta.id = "call_001"
    func_delta = MagicMock()
    func_delta.name = "search"
    func_delta.arguments = '{"q":'
    tc_delta.function = func_delta

    chunk = MagicMock()
    delta = MagicMock()
    delta.content = None
    delta.tool_calls = [tc_delta]
    delta.configure_mock(**{"reasoning_content": None, "reasoning": None})
    choice = MagicMock()
    choice.delta = delta
    chunk.choices = [choice]

    result = codec.decode_stream_chunk(chunk, state)

    # tool_calls delta 不产出 StreamDelta
    assert result is None
    # 状态中累积了 tool_call 数据
    assert 0 in state.tool_calls_raw
    assert state.tool_calls_raw[0]["id"] == "call_001"
    assert state.tool_calls_raw[0]["name"] == "search"
    assert state.tool_calls_raw[0]["arguments"] == '{"q":'


def test_decode_stream_chunk_no_content_no_delta():
    """content/reasoning/tool_calls 均无 → 返回 None，state 不变。"""
    codec = _make_codec()
    state = StreamState()

    chunk = MagicMock()
    delta = MagicMock()
    delta.content = None
    delta.tool_calls = None
    delta.configure_mock(**{"reasoning_content": None, "reasoning": None})
    choice = MagicMock()
    choice.delta = delta
    chunk.choices = [choice]

    result = codec.decode_stream_chunk(chunk, state)
    assert result is None
    assert state.text_chunks == []
