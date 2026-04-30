"""
compat — 模型/endpoint 协议兼容性描述符。

ModelCompat 描述的是"这个模型在这个 endpoint 上对协议的支持程度"，
与 ModelInfo（模型固有能力）是两个不同维度：
  - ModelInfo：模型本身能理解什么（text/image/video），不随 endpoint 变化
  - ModelCompat：endpoint 支持哪些协议特性，可能因服务商实现不同而变化

例如：deepseek-chat 通过官方 openai-compat 接口时，compat.supports_image_in_tool_result=False；
     如果某天 DeepSeek 更新了 API，这个值可以单独更新，不影响 ModelInfo。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelCompat:
    """
    模型/endpoint 协议兼容性描述符，不可变数据类。

    frozen=True 确保注册后不会被意外修改，可以安全地在多个线程间共享。

    第一阶段字段（解决现有 bug，必须填）：
        supports_image_in_tool_result — tool_result 里可以带图像 block
        supports_tools                — 支持 function calling（tools 参数）

    第二阶段字段（进阶功能，默认值覆盖大多数场景）：
        thinking_format               — 思维链输出格式
        max_tokens_field              — max_tokens 参数的字段名（新旧 API 差异）
        requires_assistant_after_tool_result — 某些模型要求 tool_result 后紧跟 assistant
    """

    # ── 第一阶段：多模态 & 工具支持 ────────────────────────────────────────────

    # tool_result.content 中是否可以包含图像 block。
    # True  → agent 可以直接把截图结果放进 tool_result 发给模型（NATIVE 路径）
    # False → agent 必须先用 VLM 把图像转成文字描述，再放进 tool_result（TRANSCRIBE 路径）
    # 纯文本模型（deepseek-chat 等）= False，支持视觉的模型（claude、gpt-4o 等）= True
    supports_image_in_tool_result: bool = True

    # 是否支持 function calling（发送 tools 参数）。
    # False → 不发 tools 参数，改用 system prompt 注入工具描述（某些 ollama 本地模型）
    supports_tools: bool = True

    # ── 第二阶段：协议细节 ─────────────────────────────────────────────────────

    # 思维链输出格式。
    # "none"     → 不支持/不需要特殊处理（绝大多数模型）
    # "openai"   → OpenAI o1/o3 格式（reasoning_content 字段）
    # "deepseek" → DeepSeek R1 格式（<think>...</think> 标签）
    thinking_format: str = "none"

    # max_tokens 参数的字段名。
    # "max_tokens"            → 旧版 OpenAI API 和大多数兼容接口
    # "max_completion_tokens" → 新版 OpenAI API（o1 系列之后）
    max_tokens_field: str = "max_tokens"

    # 某些模型（如 Mistral）要求 tool_result 消息之后必须紧跟 assistant 消息，
    # 否则报错。True 时 translator 会在 tool_result 后自动插入空 assistant 消息。
    requires_assistant_after_tool_result: bool = False

    # ── 便捷查询 ───────────────────────────────────────────────────────────────

    @property
    def has_thinking(self) -> bool:
        """是否需要处理思维链输出。"""
        return self.thinking_format != "none"

    @classmethod
    def default(cls) -> "ModelCompat":
        """
        返回默认兼容配置（适用于标准 Anthropic/OpenAI 接口）。

        supports_image_in_tool_result=True, supports_tools=True，
        适用于 claude、gpt-4o 等主流视觉模型。
        """
        return cls()

    @classmethod
    def text_only(cls) -> "ModelCompat":
        """
        返回纯文本模型的兼容配置。

        supports_image_in_tool_result=False，
        适用于 deepseek-chat、qwen2.5-72b 等不支持视觉的模型。
        """
        return cls(supports_image_in_tool_result=False)

    @classmethod
    def no_tools(cls) -> "ModelCompat":
        """
        返回不支持 function calling 的模型配置。

        适用于某些 ollama 本地模型或早期开源模型。
        """
        return cls(supports_tools=False)
