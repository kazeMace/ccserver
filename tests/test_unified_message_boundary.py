# tests/test_unified_message_boundary.py
"""边界归一：codec / serialization 层接受 UnifiedMessage 与 dict 等价。

迁移说明：
  旧测试使用 AnthropicAdapter.to_native_messages / OpenAIAdapter.to_native_messages
  和 to_wire_dict。新架构中这些职责已分别移至：
    - unified_message_to_wire（序列化工具）
    - AnthropicCodec.encode_messages
    - ChatCompletionsCodec.encode_messages
  本测试验证上述新 API 的等价行为。
"""

from ccserver.messages import UnifiedMessage, UnifiedTextBlock, unified_message_to_wire
from ccserver.model_engine.codecs.anthropic import AnthropicCodec
from ccserver.model_engine.codecs.chat_completions import ChatCompletionsCodec


def test_unified_message_to_wire_passthrough_dict():
    """unified_message_to_wire: 裸 dict → 原样透传（不复制）。"""
    d = {"role": "user", "content": "hi"}
    assert unified_message_to_wire(d) is d


def test_unified_message_to_wire_converts_unified_message():
    """unified_message_to_wire: UnifiedMessage → wire dict，content 恒为 list（不塌缩成裸 str）。"""
    um = UnifiedMessage(role="user", content=[UnifiedTextBlock(text="hi")])
    assert unified_message_to_wire(um) == {"role": "user", "content": [{"type": "text", "text": "hi"}]}


def test_anthropic_codec_accepts_unified_message():
    """AnthropicCodec.encode_messages 接受 UnifiedMessage，输出 Anthropic native 格式。

    Anthropic native 格式：content 始终是 list[dict]。
    """
    codec = AnthropicCodec()
    um = UnifiedMessage(role="user", content=[UnifiedTextBlock(text="hi")])
    result = codec.encode_messages([um], None)
    # encode_messages 返回 {"messages": [...]}
    assert result == {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]}
        ]
    }


def test_chat_completions_codec_accepts_unified_message():
    """ChatCompletionsCodec.encode_messages 接受 UnifiedMessage，输出 OpenAI native 格式。

    OpenAI 简单文本消息：content 塌缩为 str（不含图像时）。
    """
    codec = ChatCompletionsCodec()
    um = UnifiedMessage(role="user", content=[UnifiedTextBlock(text="hi")])
    result = codec.encode_messages([um], None)
    # encode_messages 返回 {"messages": [...]}
    assert result == {
        "messages": [
            {"role": "user", "content": "hi"}
        ]
    }
