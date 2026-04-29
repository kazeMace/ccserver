"""
volcano — 火山方舟 Provider Plugin。

包装现有 VolcanoAdapter（基于 volcenginesdkarkruntime.Ark SDK）。
注册 Doubao 系列模型、火山托管 DeepSeek 模型。
"""

from __future__ import annotations

import os
from typing import Any

import httpx

try:
    from volcenginesdkarkruntime import Ark
except ImportError:
    Ark = None  # type: ignore

from .base import ProviderPlugin
from ccserver.model.adapter import ModelAdapter
from ccserver.model.volcano_adapter import VolcanoAdapter
from ccserver.model.info.registry import ModelInfoRegistry


class VolcanoPlugin(ProviderPlugin):
    """火山方舟提供商插件。"""

    id = "volcano"
    name = "Volcano Engine (火山方舟)"
    transport_type = "openai-compat"

    def create_adapter(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        **config: Any,
    ) -> ModelAdapter:
        """
        创建 VolcanoAdapter。

        Args:
            api_key:  API 密钥，None 时使用 ARK_API_KEY 环境变量
            base_url: API 端点，None 时使用 ARK_BASE_URL 环境变量
        """
        if Ark is None:
            raise ImportError(
                "volcenginesdkarkruntime is required for Volcano. "
                "Install with: pip install 'volcengine-python-sdk[ark]'"
            )

        resolved_api_key = api_key or os.getenv("ARK_API_KEY", "")
        resolved_base_url = base_url or os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")

        client = Ark(
            api_key=resolved_api_key,
            base_url=resolved_base_url,
            http_client=httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=600.0),
                limits=httpx.Limits(keepalive_expiry=5),
            ),
        )
        return VolcanoAdapter(client)

    def register_models(self, registry: ModelInfoRegistry) -> None:
        """注册火山方舟模型。"""
        from ccserver.model.info.catalog import BUILTIN_VOLCANO_MODELS
        registry.register_bulk(BUILTIN_VOLCANO_MODELS)
