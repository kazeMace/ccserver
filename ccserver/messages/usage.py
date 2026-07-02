"""
ccserver/messages/usage.py

统一的 token 用量数据类，适配所有 LLM provider。
包含 Anthropic prompt caching 字段（cache_read_input_tokens / cache_creation_input_tokens）。
通过 to_dict / from_dict 在存储边界做序列化，支持旧格式向后兼容。

Unified token usage dataclass for all LLM providers.
Includes Anthropic prompt caching fields.
Serialized at storage boundaries via to_dict / from_dict, with backward compat for old dicts.
"""

from dataclasses import dataclass


@dataclass
class UnifiedUsage:
    """
    统一 token 用量。所有字段均为整数，默认值为 0。

    Unified token usage. All fields are integers, defaulting to 0.

    Fields:
        input_tokens: 输入 token 数 / Input token count
        output_tokens: 输出 token 数 / Output token count
        total_tokens: 总 token 数（input + output）/ Total token count
        cache_read_input_tokens: Anthropic prompt caching 读取命中的 token 数
                                  / Anthropic cache read hit tokens
        cache_creation_input_tokens: Anthropic prompt caching 写入缓存的 token 数
                                      / Anthropic cache write tokens
    """
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_read_input_tokens: int = 0        # Anthropic prompt caching 读取命中 token
    cache_creation_input_tokens: int = 0    # Anthropic prompt caching 写入 token

    def to_dict(self) -> dict:
        """
        序列化为字典，用于持久化存储。
        Serialize to dict for storage.

        Returns:
            dict: 包含所有字段的字典 / Dict with all fields
        """
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UnifiedUsage":
        """
        从字典反序列化，兼容旧格式（无 cache 字段时默认为 0）。
        Deserialize from dict, backward compatible (missing cache fields default to 0).

        Args:
            d: 来自存储的字典 / Dict from storage

        Returns:
            UnifiedUsage: 实例 / Instance
        """
        return cls(
            input_tokens=d.get("input_tokens", 0),
            output_tokens=d.get("output_tokens", 0),
            total_tokens=d.get("total_tokens", 0),
            cache_read_input_tokens=d.get("cache_read_input_tokens", 0),
            cache_creation_input_tokens=d.get("cache_creation_input_tokens", 0),
        )
