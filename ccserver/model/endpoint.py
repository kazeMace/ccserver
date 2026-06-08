"""
endpoint — 模型连接描述符。

ModelEndpoint 是用户配置（env / settings.json / 代码传参）的统一内部表示，
描述"如何连接到一个 LLM"。

设计原则：
  - model_id 是唯一必填项，其他字段均可通过 resolve() 自动推断补全
  - provider 是品牌标识（辅助推断），api_type 才是技术协议（决定用哪个 Adapter）
  - 同一个 model 可以走不同的 api_type（如 deepseek-chat 既支持 openai-compat 也支持 anthropic-messages）

推断链（resolve() 内部逻辑）：
  model_id → ModelInfoRegistry → provider
  provider → ProviderRegistry  → default_api_type
  api_type → 对应环境变量名     → base_url / api_key
"""

from __future__ import annotations

import os
from dataclasses import dataclass


# api_type 合法值
API_TYPE_ANTHROPIC = "anthropic-messages"
API_TYPE_OPENAI    = "openai-completions"
API_TYPE_ZHIPUAI   = "zhipuai"
API_TYPE_VOLCANO   = "volcano"

# api_type → (api_key 环境变量, base_url 环境变量) 的映射
_API_TYPE_ENV_VARS: dict[str, tuple[str, str]] = {
    API_TYPE_ANTHROPIC: ("ANTHROPIC_API_KEY",  "ANTHROPIC_BASE_URL"),
    API_TYPE_OPENAI:    ("OPENAI_API_KEY",      "OPENAI_BASE_URL"),
    API_TYPE_ZHIPUAI:   ("ZHIPUAI_API_KEY",     ""),
    API_TYPE_VOLCANO:   ("VOLC_ACCESSKEY",       ""),
}

# provider_id → default_api_type 的映射（未显式配置 api_type 时使用）
_PROVIDER_DEFAULT_API_TYPE: dict[str, str] = {
    "anthropic":  API_TYPE_ANTHROPIC,
    "openai":     API_TYPE_OPENAI,
    "openrouter": API_TYPE_OPENAI,
    "ollama":     API_TYPE_OPENAI,
    "lmstudio":   API_TYPE_OPENAI,
    "oneapi":     API_TYPE_OPENAI,
    "generic":    API_TYPE_OPENAI,
    "deepseek":   API_TYPE_OPENAI,
    "qwen":       API_TYPE_OPENAI,
    "zhipuai":    API_TYPE_ZHIPUAI,
    "volcano":    API_TYPE_VOLCANO,
    "google":     API_TYPE_OPENAI,
}

# provider_id → (api_key 环境变量, base_url 环境变量) 的映射
# 优先使用 provider 专属环境变量，找不到时 fallback 到 api_type 对应的变量
_PROVIDER_ENV_VARS: dict[str, tuple[str, str]] = {
    "anthropic":  ("ANTHROPIC_API_KEY",   "ANTHROPIC_BASE_URL"),
    "openai":     ("OPENAI_API_KEY",      "OPENAI_BASE_URL"),
    "openrouter": ("OPENROUTER_API_KEY",  "OPENROUTER_BASE_URL"),
    "ollama":     ("",                    "OLLAMA_BASE_URL"),
    "lmstudio":   ("",                    "LMSTUDIO_BASE_URL"),
    "oneapi":     ("ONEAPI_API_KEY",      "ONEAPI_BASE_URL"),
    "generic":    ("GENERIC_API_KEY",     "GENERIC_BASE_URL"),
    "deepseek":   ("DEEPSEEK_API_KEY",    "DEEPSEEK_BASE_URL"),
    "qwen":       ("DASHSCOPE_API_KEY",   "QWEN_BASE_URL"),
    "zhipuai":    ("ZHIPUAI_API_KEY",     ""),
    "volcano":    ("VOLC_ACCESSKEY",      ""),
    "google":     ("GOOGLE_API_KEY",      "GOOGLE_BASE_URL"),
}


@dataclass
class ModelEndpoint:
    """
    模型连接描述符。

    描述"用哪个模型、通过哪个协议、连接到哪个端点"的完整信息。
    是用户配置（env / settings.json / 代码传参）的统一内部表示。

    Attributes:
        model_id:  模型唯一标识，如 "claude-sonnet-4-6"、"deepseek-chat"（必填）
        api_type:  协议类型，如 "anthropic-messages"、"openai-completions"（可推断）
        provider:  提供商标识，如 "anthropic"、"deepseek"（可推断，辅助 api_type 推断）
        base_url:  API 端点 URL（可从环境变量推断）
        api_key:   API 密钥（可从环境变量推断）
    """

    # 必填：模型 ID，决定能力（ModelInfo）和归属 provider
    model_id: str

    # 可选：协议类型。未填时从 provider 的默认值推断
    # "anthropic-messages" | "openai-completions" | "zhipuai" | "volcano"
    api_type: str | None = None

    # 可选：提供商标识。未填时从 ModelInfoRegistry 查 model_id 得到
    provider: str | None = None

    # 可选：API 端点 URL。未填时从环境变量读取
    base_url: str | None = None

    # 可选：API 密钥。未填时从环境变量读取
    api_key: str | None = None

    def resolve(self) -> "ModelEndpoint":
        """
        补全所有 None 字段，返回完整确定的 endpoint。

        推断优先级（从高到低）：
          1. 显式传入的值
          2. ModelInfoRegistry（model_id → provider）
          3. ProviderRegistry 默认值（provider → api_type）
          4. 环境变量（api_type/provider → base_url / api_key）

        Returns:
            字段全部确定的新 ModelEndpoint 实例（不修改 self）
        """
        model_id = self.model_id
        provider = self.provider
        api_type = self.api_type
        base_url = self.base_url
        api_key  = self.api_key

        assert model_id, "ModelEndpoint.model_id 不能为空"

        # Step 1: 推断 provider（从 ModelInfoRegistry 查 model_id）
        if provider is None:
            from ccserver.model.info.registry import get_registry
            info = get_registry().get(model_id)
            if info is not None and info.provider:
                provider = info.provider

        # Step 2: 推断 api_type（从 provider 的默认值）
        if api_type is None:
            if provider and provider in _PROVIDER_DEFAULT_API_TYPE:
                api_type = _PROVIDER_DEFAULT_API_TYPE[provider]
            else:
                # 未知 provider，默认走 openai-compat（覆盖大多数兼容接口）
                api_type = API_TYPE_OPENAI

        # Step 3: 推断 base_url 和 api_key（从环境变量）
        # 优先使用 provider 专属环境变量，其次用 api_type 对应的变量
        if base_url is None or api_key is None:
            # 先查 provider 专属环境变量
            provider_key_var, provider_url_var = _PROVIDER_ENV_VARS.get(provider or "", ("", ""))
            # 再查 api_type 对应的环境变量（作为 fallback）
            type_key_var, type_url_var = _API_TYPE_ENV_VARS.get(api_type, ("", ""))

            if api_key is None:
                # provider 专属变量优先，其次 api_type 变量，最后空字符串
                api_key = (
                    (os.getenv(provider_key_var) if provider_key_var else None)
                    or (os.getenv(type_key_var) if type_key_var else None)
                    or ""
                )

            if base_url is None:
                base_url = (
                    (os.getenv(provider_url_var) if provider_url_var else None)
                    or (os.getenv(type_url_var) if type_url_var else None)
                    or ""
                )

        return ModelEndpoint(
            model_id=model_id,
            api_type=api_type,
            provider=provider,
            base_url=base_url or None,   # 空字符串统一为 None
            api_key=api_key or None,
        )

    @classmethod
    def from_env(cls, model_id: str | None = None) -> "ModelEndpoint":
        """
        从环境变量构造 ModelEndpoint。

        读取：
          CCSERVER_MODEL       → model_id
          CCSERVER_API_TYPE    → api_type（可选，未填时自动推断）
          ANTHROPIC_BASE_URL 等 → base_url（由 resolve() 推断）

        Args:
            model_id: 模型 ID，未填时从 CCSERVER_MODEL 环境变量读取

        Returns:
            resolve() 后的完整 ModelEndpoint
        """
        from ccserver.config import MODEL as DEFAULT_MODEL
        resolved_model_id = model_id or os.getenv("CCSERVER_MODEL") or DEFAULT_MODEL
        assert resolved_model_id, "未指定 model_id 且 CCSERVER_MODEL 环境变量未设置"

        api_type = os.getenv("CCSERVER_API_TYPE") or None

        endpoint = cls(model_id=resolved_model_id, api_type=api_type)
        return endpoint.resolve()

    def __repr__(self) -> str:
        return (
            f"ModelEndpoint(model_id={self.model_id!r}, api_type={self.api_type!r}, "
            f"provider={self.provider!r}, base_url={self.base_url!r})"
        )
