"""
ccserver/messages/thinking.py

推理配置数据类，由消费层传入 provider，Codec 负责编码为 provider 原生格式。
纯数据结构，无序列化方法，无外部依赖。

Thinking/reasoning configuration dataclass, passed from consumer layer to provider.
The Codec encodes this into the provider's native format.
Pure data structure — no serialization, no external dependencies.
"""

from dataclasses import dataclass


@dataclass
class ThinkingConfig:
    """
    推理（扩展思考）配置，由消费层传入 provider，Codec 负责编码为 provider 原生格式。
    Reasoning (extended thinking) config, passed to provider; Codec encodes to native format.

    Fields:
        enabled: 是否启用推理 / Whether reasoning is enabled
        effort: 推理力度，取值 "low"|"medium"|"high"|"xhigh"|"max"
                / Reasoning effort level: "low"|"medium"|"high"|"xhigh"|"max"
        display: 推理内容显示模式（Anthropic 4.7+ 支持）
                 "omitted"    — 不返回推理内容（默认）
                 "summarized" — 返回摘要版推理内容
                 / Display mode for thinking content (Anthropic 4.7+):
                   "omitted" (default) or "summarized"
    """
    enabled: bool = True
    effort: str = "high"        # "low"|"medium"|"high"|"xhigh"|"max"
    display: str = "omitted"    # "omitted"|"summarized"（Anthropic 4.7+ 显示模式）
