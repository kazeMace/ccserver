"""
compatibility_catalog — 各模型默认的协议兼容配置目录。

每个模型条目描述该模型/endpoint 的协议支持程度（ModelCompatibility）。
与 model_info_catalog.py（模型固有能力）是两个不同维度：
  - model_info_catalog.py：模型能理解什么（text/image/video），不随 endpoint 变化
  - compatibility_catalog.py：endpoint 支持哪些协议特性，可能因服务商实现不同而变化

查找优先级（在 CompatibilityRegistry 内部实现）：
  1. 精确匹配 (model_id, api_type)   → 最高优先级（同一模型不同协议可能有差异）
  2. 精确匹配 (model_id, None)        → 模型级默认（不区分 api_type）
  3. 精确匹配 (None, api_type)        → api_type 级默认（不区分模型）
  4. 全局默认 ModelCompatibility()           → 最保守兜底（标准接口行为）
"""

from __future__ import annotations

from .compatibility import ModelCompatibility

# ── 辅助构造函数 ────────────────────────────────────────────────────────────────────


def _c(
    model_id: str | None,
    api_type: str | None,
    **kwargs,
) -> tuple[tuple[str | None, str | None], ModelCompatibility]:
    """
    构造 compatibility catalog 条目。

    Args:
        model_id: 模型 ID，None 表示匹配该 api_type 下所有模型
        api_type: api_type，None 表示匹配该 model_id 下所有协议
        **kwargs: ModelCompatibility 字段覆盖值

    Returns:
        ((model_id, api_type), ModelCompatibility) 元组，供 CompatibilityRegistry 消费
    """
    return (model_id, api_type), ModelCompatibility(**kwargs)


# ── Anthropic 系列（原生支持多模态 + tools）──────────────────────────────────────────
# Claude 系列通过 anthropic-messages 协议时，supports_image_in_tool_result=True（原生支持）

COMPATIBILITY_ANTHROPIC_API = [
    # api_type=anthropic-messages 的全局默认：支持图像 tool_result
    _c(None, "anthropic-messages",
       supports_image_in_tool_result=True,
       supports_tools=True),
]


# ── OpenAI 系列（openai-completions）────────────────────────────────────────────────
# GPT-4o 等视觉模型支持图像 tool_result（通过 data URI 传递）
# 纯文本 OpenAI 模型（o1 等）不支持图像

COMPATIBILITY_OPENAI_API = [
    # gpt-4o 系列：支持图像 tool_result
    _c("gpt-4o", None,
       supports_image_in_tool_result=True,
       supports_tools=True),
    _c("gpt-4o-mini", None,
       supports_image_in_tool_result=True,
       supports_tools=True),
    _c("gpt-4-turbo", None,
       supports_image_in_tool_result=True,
       supports_tools=True),
    _c("gpt-4.1", None,
       supports_image_in_tool_result=True,
       supports_tools=True),
    # o3 / o4-mini：支持视觉，使用新版 max_tokens 字段名
    _c("o3", None,
       supports_image_in_tool_result=True,
       supports_tools=True,
       max_tokens_field="max_completion_tokens"),
    _c("o4-mini", None,
       supports_image_in_tool_result=True,
       supports_tools=True,
       max_tokens_field="max_completion_tokens"),
    # api_type=openai-completions 的全局默认：不假设支持图像 tool_result
    # （保守策略，防止未知模型出错；有视觉能力的模型应在上方单独覆盖）
    _c(None, "openai-completions",
       supports_image_in_tool_result=False,
       supports_tools=True),
]


# ── DeepSeek 系列（已移除）─────────────────────────────────────────────────────────
# deepseek 已从内置目录移除（无专属 Provider）。如经 generic 使用 deepseek，
# 可在此按需补充条目，例如 thinking_format="deepseek"（R1 的 <think> 标签）。


# ── 汇总 ──────────────────────────────────────────────────────────────────────────
# 所有内置 compatibility 条目，供 CompatibilityRegistry 加载

BUILTIN_COMPATIBILITY_CATALOG: list[tuple[tuple[str | None, str | None], ModelCompatibility]] = []
BUILTIN_COMPATIBILITY_CATALOG.extend(COMPATIBILITY_ANTHROPIC_API)
BUILTIN_COMPATIBILITY_CATALOG.extend(COMPATIBILITY_OPENAI_API)
