"""
adapter — ModelAdapter 抽象基类。

所有 LLM 后端（Anthropic、OpenAI 兼容接口等）均实现此接口，
Agent 和 Compactor 只依赖此抽象，不直接引用具体 SDK。

设计原则：
  - 调用方（Agent、Compactor）永远使用 Anthropic block 格式传 messages
  - adapter 内部负责协议转换，调用方不感知差异
  - model_info 和 compat 字段让调用方可以直接查询模型能力，无需再去注册表查
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

from ccserver.model.compat import ModelCompat

if TYPE_CHECKING:
    from ccserver.model.info.model_info import ModelInfo


class ModelAdapter(ABC):
    """
    统一的 LLM 调用接口。

    子类需实现 create() 和 stream()，分别对应非流式和流式调用。
    返回值与 Anthropic SDK 的 Message / AsyncMessageStream 保持相同结构，
    调用方通过 response.content[i].text、response.stop_reason 等字段访问结果。

    Attributes:
        model_info: 模型固有能力（input_types / context_window 等），由 AdapterFactory 注入。
                    None 表示未知模型，能力查询走保守路径。
        compat:     协议兼容性（supports_image_in_tool_result / supports_tools 等），
                    由 AdapterFactory 注入，默认值覆盖标准 Anthropic/OpenAI 接口。
    """

    # 模型固有能力（由 AdapterFactory 构造时注入，默认 None 表示未知）
    # 类型注解使用字符串前向引用，避免循环导入
    model_info: "ModelInfo | None" = None

    # 协议兼容性（由 AdapterFactory 构造时注入，默认值适用于标准接口）
    compat: ModelCompat = ModelCompat()

    # ── 便捷能力查询（代理 model_info 和 compat）──────────────────────────────

    @property
    def supports_image(self) -> bool:
        """
        模型是否理解图像输入（模型固有能力）。

        True  → 模型原生支持视觉（claude、gpt-4o 等）
        False → 纯文本模型（deepseek-chat、qwen2.5-72b 等）或模型信息未知
        """
        if self.model_info is None:
            return False
        return self.model_info.supports_image

    @property
    def supports_image_in_tool_result(self) -> bool:
        """
        该 endpoint 是否允许 tool_result.content 中携带图像 block（协议兼容性）。

        True  → agent 可直接把截图放进 tool_result（NATIVE 路径）
        False → agent 必须先用 VLM 把截图转成文字描述（TRANSCRIBE 路径）
        """
        return self.compat.supports_image_in_tool_result

    @property
    def supports_tools(self) -> bool:
        """
        该 endpoint 是否支持 function calling（发送 tools 参数）。

        False → 不发 tools 参数，改用 system prompt 注入工具描述
        """
        return self.compat.supports_tools

    @abstractmethod
    async def create(
        self,
        *,
        model: str,
        messages: list[dict],
        max_tokens: int,
        system: list[dict] | str | None = None,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ):
        """
        非流式调用，返回完整的 Message 对象。
        等价于 anthropic_client.messages.create(...)
        """

    @abstractmethod
    def stream(
        self,
        *,
        model: str,
        messages: list[dict],
        max_tokens: int,
        system: list[dict] | str | None = None,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ):
        """
        流式调用，返回可用于 async with 的 context manager。
        等价于 anthropic_client.messages.stream(...)
        """
