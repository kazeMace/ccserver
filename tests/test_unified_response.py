"""
tests/test_unified_response.py

TDD 测试：UnifiedResponse、UnifiedStreamDelta、StreamState

测试覆盖：
- UnifiedResponse 默认值
- UnifiedResponse 带 tool_calls
- UnifiedResponse 带 usage
- UnifiedResponse provider_data 可存任意 dict
- UnifiedStreamDelta 基本属性
- StreamState 所有字段初始为空
"""

import pytest

from ccserver.messages.unified_response import UnifiedResponse
from ccserver.messages.stream import UnifiedStreamDelta, StreamState
from ccserver.messages.tool_call import UnifiedToolCall
from ccserver.messages.usage import UnifiedUsage


# ─────────────────────────────────────────────────────────────
# UnifiedResponse 默认值
# ─────────────────────────────────────────────────────────────

def test_response_defaults():
    """UnifiedResponse 所有字段的默认值"""
    resp = UnifiedResponse()
    assert resp.content == ""
    assert resp.thinking == ""
    assert resp.tool_calls == []
    assert resp.stop_reason == "end_turn"
    assert resp.usage is None
    assert resp.provider_data is None


def test_response_with_tool_calls():
    """赋值 UnifiedToolCall 列表"""
    calls = [
        UnifiedToolCall(id="tc-1", name="Read", input={"path": "a.py"}),
        UnifiedToolCall(id="tc-2", name="Write", input={"path": "b.py", "content": "x"}),
    ]
    resp = UnifiedResponse(tool_calls=calls)
    assert len(resp.tool_calls) == 2
    assert resp.tool_calls[0].id == "tc-1"
    assert resp.tool_calls[1].name == "Write"


def test_response_with_usage():
    """赋值 UnifiedUsage"""
    usage = UnifiedUsage(input_tokens=10, output_tokens=20, total_tokens=30)
    resp = UnifiedResponse(usage=usage)
    assert resp.usage is not None
    assert resp.usage.input_tokens == 10
    assert resp.usage.output_tokens == 20
    assert resp.usage.total_tokens == 30


def test_response_provider_data():
    """provider_data 可存任意 dict，运行时内存字段"""
    data = {"signature": "sig-abc", "call_id": "c-1"}
    resp = UnifiedResponse(provider_data=data)
    assert resp.provider_data == data
    assert resp.provider_data["signature"] == "sig-abc"


# ─────────────────────────────────────────────────────────────
# UnifiedStreamDelta
# ─────────────────────────────────────────────────────────────

def test_stream_delta():
    """UnifiedStreamDelta 基本属性"""
    delta = UnifiedStreamDelta(kind="text", text="abc")
    assert delta.kind == "text"
    assert delta.text == "abc"


def test_stream_delta_thinking():
    """UnifiedStreamDelta kind="thinking" 路径"""
    delta = UnifiedStreamDelta(kind="thinking", text="let me reason")
    assert delta.kind == "thinking"
    assert delta.text == "let me reason"


# ─────────────────────────────────────────────────────────────
# StreamState 默认值
# ─────────────────────────────────────────────────────────────

def test_stream_state_defaults():
    """StreamState 所有字段初始为空"""
    state = StreamState()
    assert state.text_chunks == []
    assert state.thinking_chunks == []
    assert state.tool_calls_raw == {}
    assert state.stop_reason_raw is None
    assert state.usage_raw is None
