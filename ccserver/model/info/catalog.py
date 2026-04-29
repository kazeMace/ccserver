"""
catalog — 内置模型能力目录。

声明已知主流 LLM 模型的 input_types（text/image/video/audio/file）。
Provider 插件在 register_models() 中会补充更多模型。
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


# ── DeepSeek ─────────────────────────────────────────────────────────────────────
# 纯文本模型，不支持多模态

BUILTIN_DEEPSEEK_MODELS = [
    _m("deepseek-chat",                  "DeepSeek V3",                frozenset({"text"}),           65536,  8192,  priority=30,  provider="deepseek"),
    _m("deepseek-reasoner",              "DeepSeek R1",                frozenset({"text"}),           65536,  8192,  priority=30,  provider="deepseek"),
]


# ── Qwen / Tongyi ────────────────────────────────────────────────────────────────
# Qwen-VL 系列支持 text + image，Qwen 纯文本系列仅 text

BUILTIN_QWEN_MODELS = [
    # VL 多模态系列
    _m("qwen2.5-vl-72b-instruct",        "Qwen2.5-VL 72B Instruct",   frozenset({"text", "image"}),  131072, 8192,  priority=35,  provider="qwen"),
    _m("qwen2.5-vl-7b-instruct",         "Qwen2.5-VL 7B Instruct",    frozenset({"text", "image"}),  131072, 8192,  priority=30,  provider="qwen"),
    _m("qwen-vl-max-latest",             "Qwen VL Max",                frozenset({"text", "image"}),  32768,  8192,  priority=40,  provider="qwen"),
    # 纯文本系列
    _m("qwen2.5-72b-instruct",           "Qwen2.5 72B Instruct",      frozenset({"text"}),           131072, 8192,  priority=25,  provider="qwen"),
    _m("qwen2.5-32b-instruct",           "Qwen2.5 32B Instruct",      frozenset({"text"}),           131072, 8192,  priority=25,  provider="qwen"),
    _m("qwen2.5-7b-instruct",            "Qwen2.5 7B Instruct",       frozenset({"text"}),           131072, 8192,  priority=20,  provider="qwen"),
    # Qwen3 系列
    _m("qwen3-235b-a22b",                "Qwen3 235B-A22B",           frozenset({"text", "image"}),  131072, 8192,  priority=45,  provider="qwen"),
    _m("qwen3-30b-a3b",                  "Qwen3 30B-A3B",             frozenset({"text", "image"}),  131072, 8192,  priority=35,  provider="qwen"),
]


# ── GLM / ZhipuAI ────────────────────────────────────────────────────────────────
# GLM-5V 多模态系列，支持 text + image + video + file

BUILTIN_ZHIPUAI_MODELS = [
    _m("glm-5v-turbo",                   "GLM-5V Turbo",              frozenset({"text", "image", "video", "file"}), 131072, 8192, priority=45, provider="zhipuai"),
    _m("glm-4.5",                        "GLM-4.5",                   frozenset({"text"}),           131072, 8192,  priority=30,  provider="zhipuai"),
]


# ── Google ───────────────────────────────────────────────────────────────────────
# Gemini 系列支持 text + image

BUILTIN_GOOGLE_MODELS = [
    _m("gemini-2.0-flash-exp",           "Gemini 2.0 Flash",          frozenset({"text", "image"}),  1048576, 8192, priority=35, provider="google"),
    _m("gemini-2.5-pro-exp-03-25",       "Gemini 2.5 Pro",            frozenset({"text", "image"}),  1048576, 8192, priority=45, provider="google"),
    _m("gemini-2.5-flash-lite",          "Gemini 2.5 Flash Lite",     frozenset({"text", "image"}),  1048576, 8192, priority=30, provider="google"),
]


# ── 火山方舟 ─────────────────────────────────────────────────────────────────────

BUILTIN_VOLCANO_MODELS = [
    _m("doubao-pro-32k",                 "Doubao Pro 32K",            frozenset({"text"}),           32768,  4096,  priority=25,  provider="volcano"),
    _m("doubao-pro-128k",                "Doubao Pro 128K",           frozenset({"text"}),           131072, 4096,  priority=30,  provider="volcano"),
    _m("deepseek-r1-0528",               "DeepSeek R1 (火山)",         frozenset({"text"}),           65536,  8192,  priority=30,  provider="volcano"),
]


# ── 汇总 ─────────────────────────────────────────────────────────────────────────

# 所有内置模型列表，遍历注册到 ModelInfoRegistry
BUILTIN_MODEL_CATALOG: list[ModelInfo] = []
BUILTIN_MODEL_CATALOG.extend(BUILTIN_ANTHROPIC_MODELS)
BUILTIN_MODEL_CATALOG.extend(BUILTIN_OPENAI_MODELS)
BUILTIN_MODEL_CATALOG.extend(BUILTIN_DEEPSEEK_MODELS)
BUILTIN_MODEL_CATALOG.extend(BUILTIN_QWEN_MODELS)
BUILTIN_MODEL_CATALOG.extend(BUILTIN_ZHIPUAI_MODELS)
BUILTIN_MODEL_CATALOG.extend(BUILTIN_GOOGLE_MODELS)
BUILTIN_MODEL_CATALOG.extend(BUILTIN_VOLCANO_MODELS)
