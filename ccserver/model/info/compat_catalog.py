"""
compat_catalog — 各模型默认的协议兼容配置目录。

每个模型条目描述该模型/endpoint 的协议支持程度（ModelCompat）。
与 catalog.py（模型固有能力）是两个不同维度：
  - catalog.py：模型能理解什么（text/image/video），不随 endpoint 变化
  - compat_catalog.py：endpoint 支持哪些协议特性，可能因服务商实现不同而变化

查找优先级（在 CompatRegistry 内部实现）：
  1. 精确匹配 (model_id, api_type)   → 最高优先级（同一模型不同协议可能有差异）
  2. 精确匹配 (model_id, None)        → 模型级默认（不区分 api_type）
  3. 精确匹配 (None, api_type)        → api_type 级默认（不区分模型）
  4. 全局默认 ModelCompat()           → 最保守兜底（标准接口行为）
"""

from __future__ import annotations

from ccserver.model.compat import ModelCompat

# ── 辅助构造函数 ────────────────────────────────────────────────────────────────────


def _c(
    model_id: str | None,
    api_type: str | None,
    **kwargs,
) -> tuple[tuple[str | None, str | None], ModelCompat]:
    """
    构造 compat catalog 条目。

    Args:
        model_id: 模型 ID，None 表示匹配该 api_type 下所有模型
        api_type: api_type，None 表示匹配该 model_id 下所有协议
        **kwargs: ModelCompat 字段覆盖值

    Returns:
        ((model_id, api_type), ModelCompat) 元组，供 CompatRegistry 消费
    """
    return (model_id, api_type), ModelCompat(**kwargs)


# ── Anthropic 系列（原生支持多模态 + tools）──────────────────────────────────────────
# Claude 系列通过 anthropic-messages 协议时，supports_image_in_tool_result=True（原生支持）

COMPAT_ANTHROPIC_API = [
    # api_type=anthropic-messages 的全局默认：支持图像 tool_result
    _c(None, "anthropic-messages",
       supports_image_in_tool_result=True,
       supports_tools=True),
]


# ── OpenAI 系列（openai-completions）────────────────────────────────────────────────
# GPT-4o 等视觉模型支持图像 tool_result（通过 data URI 传递）
# 纯文本 OpenAI 模型（o1 等）不支持图像

COMPAT_OPENAI_API = [
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


# ── DeepSeek 系列（纯文本，不支持图像 tool_result）──────────────────────────────────
# deepseek-chat 和 deepseek-reasoner 均为纯文本模型

COMPAT_DEEPSEEK = [
    _c("deepseek-chat", None,
       supports_image_in_tool_result=False,
       supports_tools=True,
       thinking_format="none"),
    _c("deepseek-reasoner", None,
       supports_image_in_tool_result=False,
       supports_tools=True,
       thinking_format="deepseek"),  # DeepSeek R1 输出 <think>...</think> 标签
]


# ── Qwen 系列 ───────────────────────────────────────────────────────────────────────
# VL 系列支持图像 tool_result，纯文本系列不支持

COMPAT_QWEN = [
    # Qwen VL 多模态系列：支持图像 tool_result
    _c("qwen2.5-vl-72b-instruct", None,
       supports_image_in_tool_result=True,
       supports_tools=True),
    _c("qwen2.5-vl-7b-instruct", None,
       supports_image_in_tool_result=True,
       supports_tools=True),
    _c("qwen-vl-max-latest", None,
       supports_image_in_tool_result=True,
       supports_tools=True),
    _c("qwen3-235b-a22b", None,
       supports_image_in_tool_result=True,
       supports_tools=True),
    _c("qwen3-30b-a3b", None,
       supports_image_in_tool_result=True,
       supports_tools=True),
    # 纯文本 Qwen 系列
    _c("qwen2.5-72b-instruct", None,
       supports_image_in_tool_result=False,
       supports_tools=True),
    _c("qwen2.5-32b-instruct", None,
       supports_image_in_tool_result=False,
       supports_tools=True),
    _c("qwen2.5-7b-instruct", None,
       supports_image_in_tool_result=False,
       supports_tools=True),
]


# ── GLM / ZhipuAI 系列 ───────────────────────────────────────────────────────────
# GLM-5V 支持多模态，GLM-4.5 纯文本

COMPAT_ZHIPUAI = [
    _c("glm-5v-turbo", None,
       supports_image_in_tool_result=True,
       supports_tools=True),
    _c("glm-4.5", None,
       supports_image_in_tool_result=False,
       supports_tools=True),
]


# ── 火山方舟 ───────────────────────────────────────────────────────────────────────
# 当前注册模型均为纯文本

COMPAT_VOLCANO = [
    _c("doubao-pro-32k", None,
       supports_image_in_tool_result=False,
       supports_tools=True),
    _c("doubao-pro-128k", None,
       supports_image_in_tool_result=False,
       supports_tools=True),
    _c("deepseek-r1-0528", None,
       supports_image_in_tool_result=False,
       supports_tools=True,
       thinking_format="deepseek"),
]


# ── 汇总 ──────────────────────────────────────────────────────────────────────────
# 所有内置 compat 条目，供 CompatRegistry 加载

BUILTIN_COMPAT_CATALOG: list[tuple[tuple[str | None, str | None], ModelCompat]] = []
BUILTIN_COMPAT_CATALOG.extend(COMPAT_ANTHROPIC_API)
BUILTIN_COMPAT_CATALOG.extend(COMPAT_OPENAI_API)
BUILTIN_COMPAT_CATALOG.extend(COMPAT_DEEPSEEK)
BUILTIN_COMPAT_CATALOG.extend(COMPAT_QWEN)
BUILTIN_COMPAT_CATALOG.extend(COMPAT_ZHIPUAI)
BUILTIN_COMPAT_CATALOG.extend(COMPAT_VOLCANO)
