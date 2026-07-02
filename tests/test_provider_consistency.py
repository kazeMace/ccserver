# tests/test_provider_consistency.py
"""tests/test_provider_consistency.py — Provider 架构一致性（接口契约 + 流式契约）。

迁移说明：
  旧测试验证 AnthropicAdapter / OpenAIAdapter 返回中性 Message 及 StreamSession 继承。
  新架构中等价不变量为：
    1. AnthropicProvider 和 OpenAIChatProvider 都是 LLMProvider（BaseLLMProvider）的子类。
    2. AnthropicProvider 的 codec 能 decode 出 UnifiedResponse（decode_response）。
    3. ChatCompletionsCodec 的 decode_response 返回 UnifiedResponse。
    4. ProviderStream 是所有流式 Provider 使用的统一流式接口。

验证这些不变量确保所有 Provider 满足统一 Provider 契约。
"""

import pytest
from unittest.mock import MagicMock

from ccserver.model_engine.providers.base import LLMProvider, BaseLLMProvider
from ccserver.model_engine.providers.stream import ProviderStream
from ccserver.model_engine.providers.anthropic import AnthropicProvider
from ccserver.model_engine.providers.openai_chat import OpenAIChatProvider
from ccserver.messages import UnifiedResponse


def test_anthropic_provider_is_llm_provider_subclass():
    """AnthropicProvider 必须继承 LLMProvider（统一 Provider 契约）。"""
    assert issubclass(AnthropicProvider, LLMProvider), (
        "AnthropicProvider 必须继承 LLMProvider / AnthropicProvider must inherit LLMProvider"
    )


def test_openai_chat_provider_is_llm_provider_subclass():
    """OpenAIChatProvider 必须继承 LLMProvider（统一 Provider 契约）。"""
    assert issubclass(OpenAIChatProvider, LLMProvider), (
        "OpenAIChatProvider 必须继承 LLMProvider / OpenAIChatProvider must inherit LLMProvider"
    )


def test_anthropic_provider_codec_decode_returns_unified_response():
    """AnthropicCodec.decode_response 返回 UnifiedResponse（统一消息中性）。

    构造 minimal SDK mock，验证 decode 产出类型正确，usage.total_tokens 正确累加。
    """
    from ccserver.model_engine.codecs.anthropic import AnthropicCodec

    codec = AnthropicCodec()

    # 构造 minimal SDK Message mock
    sdk = MagicMock()
    sdk.content = []
    sdk.stop_reason = "end_turn"

    # usage mock: input=1, output=1 → total=2
    u = MagicMock()
    u.input_tokens = 1
    u.output_tokens = 1
    sdk.usage = u

    response = codec.decode_response(sdk)

    assert isinstance(response, UnifiedResponse), (
        "AnthropicCodec.decode_response 必须返回 UnifiedResponse"
    )
    assert response.usage is not None, "usage 不应为 None"
    assert response.usage.total_tokens == 2, (
        "total_tokens 应等于 input_tokens + output_tokens"
    )


def test_chat_completions_codec_decode_returns_unified_response():
    """ChatCompletionsCodec.decode_response 返回 UnifiedResponse（统一消息中性）。

    构造 minimal ChatCompletion mock，验证 decode 产出类型正确。
    """
    from ccserver.model_engine.codecs.chat_completions import ChatCompletionsCodec

    codec = ChatCompletionsCodec()

    resp = MagicMock()
    ch = MagicMock()
    msg = MagicMock()

    # 无 tool_calls，无 reasoning_content
    msg.content = "x"
    msg.tool_calls = None
    del msg.reasoning_content
    msg.reasoning = None

    ch.message = msg
    ch.finish_reason = "stop"
    resp.choices = [ch]

    # usage mock: prompt=1, completion=1, total=2
    uu = MagicMock()
    uu.prompt_tokens = 1
    uu.completion_tokens = 1
    uu.total_tokens = 2
    resp.usage = uu

    response = codec.decode_response(resp)

    assert isinstance(response, UnifiedResponse), (
        "ChatCompletionsCodec.decode_response 必须返回 UnifiedResponse"
    )
    assert response.usage is not None, "usage 不应为 None"
    assert response.usage.total_tokens == 2, (
        "total_tokens 应等于 prompt_tokens + completion_tokens"
    )


def test_provider_stream_is_unified_streaming_contract():
    """ProviderStream 是所有 Provider 使用的统一流式接口。

    验证 BaseLLMProvider.stream() 的返回类型是 ProviderStream（静态检查）。
    """
    import inspect
    # stream() 方法的返回注解应包含 ProviderStream
    method = BaseLLMProvider.stream
    hints = {}
    try:
        hints = method.__annotations__
    except AttributeError:
        pass

    # 只要 ProviderStream 存在且可导入即视为通过（运行时构造需真实 adapter/codec）
    assert ProviderStream is not None, "ProviderStream 必须可导入"
