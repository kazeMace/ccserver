"""
base — ProviderPlugin 协议定义。

每个 LLM 提供商通过实现此协议注册到 ProviderRegistry。
核心方法：create_adapter()（创建 ModelAdapter）和 register_models()（声明模型能力）。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ccserver.model.adapter import ModelAdapter
from ccserver.model.info.registry import ModelInfoRegistry


@runtime_checkable
class ProviderPlugin(Protocol):
    """
    LLM 提供商插件协议。

    每个提供商（Anthropic、OpenAI、Qwen、GLM 等）都需要实现此协议。
    通过 ProviderRegistry 注册后，系统可动态创建对应的 ModelAdapter。

    Attributes:
        id:             唯一标识，如 "anthropic"、"openai"、"zhipuai"
        name:           人类可读名称，如 "Anthropic"、"智谱 GLM"
        transport_type: 传输协议类型："anthropic" | "openai-compat" | "zhipuai" | "google-genai"
    """

    @property
    def id(self) -> str:
        """唯一标识，如 "anthropic"、"openai"、zhipuai"。"""
        ...

    @property
    def name(self) -> str:
        """人类可读名称，如 "Anthropic"、"OpenAI"、"智谱 GLM"。"""
        ...

    @property
    def transport_type(self) -> str:
        """
        传输协议类型。

        可选值：
        - "anthropic":      Anthropic Messages API
        - "openai-compat":  OpenAI Chat Completions 兼容 API
        - "zhipuai":        智谱 GLM zai-sdk
        - "google-genai":   Google Generative AI
        """
        ...

    def create_adapter(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        **config: Any,
    ) -> ModelAdapter:
        """
        创建 ModelAdapter 实例。

        Args:
            api_key:  API 密钥，None 时从环境变量获取
            base_url: API 端点 URL，None 时使用默认值
            **config: 额外的提供商特定配置

        Returns:
            ModelAdapter 实例
        """
        ...

    def register_models(self, registry: ModelInfoRegistry) -> None:
        """
        向 ModelInfoRegistry 注册本提供商支持的模型及其能力。

        在 ProviderRegistry 初始化时自动调用，每个插件将自身支持的模型
        及其 input_types 注册到全局 ModelInfoRegistry。

        Args:
            registry: 全局 ModelInfoRegistry 实例
        """
        ...
