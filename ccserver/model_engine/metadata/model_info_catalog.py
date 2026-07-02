"""
model_info_catalog — 内置模型能力目录。

声明已知主流 LLM 模型的 input_types（text/image/video/audio/file）。
由 ModelInfoRegistry._init_defaults() 全量加载；如需扩展，直接在此追加条目。
"""

from __future__ import annotations

from .model_info import ModelInfo


# ── 辅助函数 ────────────────────────────────────────────────────────────────────


def _m(
    model_id: str,
    name: str = "",
    input_types: frozenset | None = None,
    context_window: int = 0,
    max_tokens: int = 0,
    priority: int = 0,
    provider: str = "",
) -> ModelInfo:
    """简化的 ModelInfo 构造辅助函数，减少样板代码。"""
    return ModelInfo(
        model_id=model_id,
        name=name or model_id,
        input_types=input_types or frozenset({"text"}),
        context_window=context_window,
        max_tokens=max_tokens,
        priority=priority,
        provider=provider,
    )


# ── Anthropic ────────────────────────────────────────────────────────────────────
# Claude 全系列支持 text + image

BUILTIN_ANTHROPIC_MODELS = [
    _m("claude-sonnet-4-6",              "Claude Sonnet 4.6",          frozenset({"text", "image"}),  200000, 8192,  priority=100, provider="anthropic"),
    _m("claude-3-5-sonnet-latest",       "Claude 3.5 Sonnet",          frozenset({"text", "image"}),  200000, 8192,  priority=90,  provider="anthropic"),
    _m("claude-3-opus-latest",           "Claude 3 Opus",              frozenset({"text", "image"}),  200000, 4096,  priority=80,  provider="anthropic"),
    _m("claude-3-5-haiku-latest",        "Claude 3.5 Haiku",           frozenset({"text", "image"}),  200000, 4096,  priority=70,  provider="anthropic"),
    _m("claude-3-haiku-20240307",        "Claude 3 Haiku",             frozenset({"text", "image"}),  200000, 4096,  priority=60,  provider="anthropic"),
]


# ── OpenAI ───────────────────────────────────────────────────────────────────────
# GPT-4o 系列支持 text + image，GPT-4-turbo 也支持图像

BUILTIN_OPENAI_MODELS = [
    _m("gpt-4o",                         "GPT-4o",                     frozenset({"text", "image"}),  128000, 16384, priority=55,  provider="openai"),
    _m("gpt-4o-mini",                    "GPT-4o Mini",                frozenset({"text", "image"}),  128000, 16384, priority=50,  provider="openai"),
    _m("gpt-4-turbo",                    "GPT-4 Turbo",                frozenset({"text", "image"}),  128000, 4096,  priority=40,  provider="openai"),
    _m("gpt-4.1",                        "GPT-4.1",                    frozenset({"text", "image"}),  1000000, 32768, priority=45, provider="openai"),
    _m("o3",                             "o3",                         frozenset({"text", "image"}),  200000,  100000, priority=60, provider="openai"),
    _m("o4-mini",                        "o4-mini",                    frozenset({"text", "image"}),  200000,  100000, priority=55, provider="openai"),
]


# ── DeepSeek / Google ────────────────────────────────────────────────────────────
# 已移除：deepseek / google 没有专属 Provider 实现，曾导致「能查到能力却造不出
# adapter」的不一致。如需使用这些模型，请走 generic（OpenAI 兼容）provider 自行配置
# base_url / api_key，并按需在 compatibility_catalog 中补充协议兼容性。


# ── 汇总 ─────────────────────────────────────────────────────────────────────────

# 所有内置模型列表，遍历注册到 ModelInfoRegistry
BUILTIN_MODEL_CATALOG: list[ModelInfo] = []
BUILTIN_MODEL_CATALOG.extend(BUILTIN_ANTHROPIC_MODELS)
BUILTIN_MODEL_CATALOG.extend(BUILTIN_OPENAI_MODELS)
