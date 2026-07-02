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
  provider → provider_catalog  → default_api_type
  api_type → 对应环境变量名     → base_url / api_key
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Provider 元数据单一事实源（SSOT）。
# api_type 常量在 providers.py 中定义，此处 import 并 re-export，
# 使 `from ccserver.model_engine import ModelEndpoint` 门面及内部引用可用。
from .providers import (
    API_TYPE_ANTHROPIC,
    API_TYPE_OPENAI,
    PROVIDER_SPEC_BY_ID,
)

# api_type → (api_key 环境变量, base_url 环境变量) 的映射。
# 这是「provider 未知、仅知道 api_type」时的回退来源（与 provider 维度正交）。
# 注意：provider 维度的 env 变量已迁移到 provider_catalog.PROVIDER_SPECS，
#       此表只保留 api_type 级回退，不再与 provider 表重复。
_API_TYPE_ENV_VARS: dict[str, tuple[str, str]] = {
    API_TYPE_ANTHROPIC: ("ANTHROPIC_API_KEY",  "ANTHROPIC_BASE_URL"),
    API_TYPE_OPENAI:    ("OPENAI_API_KEY",      "OPENAI_BASE_URL"),
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
    # "anthropic-messages" | "openai-completions"
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
          3. provider_catalog 默认值（provider → api_type）
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
            from ccserver.model_engine.metadata.model_info_registry import get_registry
            info = get_registry().get(model_id)
            if info is not None and info.provider:
                provider = info.provider

        # Step 2: 推断 api_type（从 provider 的默认值，查 SSOT）
        if api_type is None:
            spec = PROVIDER_SPEC_BY_ID.get(provider or "")
            if spec is not None:
                api_type = spec.api_type
            else:
                # 未知 provider，默认走 openai-compat（覆盖大多数兼容接口）
                api_type = API_TYPE_OPENAI

        # Step 3: 推断 base_url 和 api_key（从环境变量）
        # 优先使用 provider 专属环境变量（来自 SSOT），其次用 api_type 对应的变量
        if base_url is None or api_key is None:
            # 先查 provider 专属环境变量（provider_catalog 的 ProviderSpec）
            spec = PROVIDER_SPEC_BY_ID.get(provider or "")
            provider_key_var = spec.env_api_key if spec is not None else ""
            provider_url_var = spec.env_base_url if spec is not None else ""
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
                # 优先级：provider 专属 env > api_type env > provider 内置默认端点 > 空
                # 内置默认端点（如 ollama 的 http://localhost:11434/v1）来自 SSOT，
                # 保证未配置 env 时仍能连到正确的本地/聚合端点。
                provider_default_url = spec.default_base_url if spec is not None else ""
                base_url = (
                    (os.getenv(provider_url_var) if provider_url_var else None)
                    or (os.getenv(type_url_var) if type_url_var else None)
                    or provider_default_url
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
        from ccserver.configuration import get_process_config
        default_model = get_process_config().model.model_id
        resolved_model_id = model_id or os.getenv("CCSERVER_MODEL") or default_model
        assert resolved_model_id, "未指定 model_id 且 CCSERVER_MODEL 环境变量未设置"

        api_type = os.getenv("CCSERVER_API_TYPE") or None

        endpoint = cls(model_id=resolved_model_id, api_type=api_type)
        return endpoint.resolve()

    def __repr__(self) -> str:
        return (
            f"ModelEndpoint(model_id={self.model_id!r}, api_type={self.api_type!r}, "
            f"provider={self.provider!r}, base_url={self.base_url!r})"
        )
