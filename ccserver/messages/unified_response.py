"""
ccserver/messages/unified_response.py

统一 LLM 响应数据类（Codec.decode_response 的输出）。
纯数据结构，无方法，无外部依赖（除 dataclass）。

Unified LLM response dataclass (output of Codec.decode_response).
Pure data structure with no methods and no external dependencies (except dataclass).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UnifiedResponse:
    """
    统一 LLM 响应（Codec.decode_response 的输出）。
    Unified LLM response (output of Codec.decode_response).

    Fields:
        content:       响应文本内容，默认空字符串
                       Response text content, defaults to empty string
        thinking:      思考链文本，默认空字符串（extended thinking 场景使用）
                       Reasoning text, defaults to empty string (used in extended thinking)
        tool_calls:    工具调用列表，每个元素为 UnifiedToolCall 实例
                       List of tool calls; each element is a UnifiedToolCall instance
        stop_reason:   停止原因，默认 "end_turn"
                       Stop reason, defaults to "end_turn"
        usage:         token 用量，None 表示 provider 未返回用量信息
                       Token usage, None means provider did not return usage info
        provider_data: provider 专属字段（签名回放等），运行时内存，不持久化、不序列化
                       Provider-specific fields (e.g. signature replay), runtime-only,
                       not persisted or serialized

    设计说明 / Design note:
        这是纯数据类（无方法），所有行为由 Codec 和 ProviderStream 负责。
        This is a pure data class (no methods); all behavior lives in Codec and ProviderStream.
    """

    content: str = ""                            # 响应正文文本 / Response body text
    thinking: str = ""                           # 思考链文本 / Reasoning text
    tool_calls: list = field(default_factory=list)  # list[UnifiedToolCall]
    stop_reason: str = "end_turn"                # 停止原因 / Stop reason
    usage: "object | None" = None               # UnifiedUsage | None
    provider_data: "dict | None" = None         # 运行时内存，不序列化 / Runtime only, not serialized
