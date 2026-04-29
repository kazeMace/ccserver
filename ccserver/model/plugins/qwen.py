"""
qwen — 通义千问 (Qwen) Provider Plugin。

OpenAI 兼容 API，使用现有 OpenAIAdapter 实现。
端点：https://dashscope.aliyuncs.com/compatible-mode/v1（中国区）或 dashscope-intl.aliyuncs.com（国际区）。

注册 Qwen 系列模型（含 VL 多模态和纯文本系列）。
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from openai import AsyncOpenAI

from .base import ProviderPlugin
from ccserver.model.adapter import ModelAdapter
from ccserver.model.openai_adapter import OpenAIAdapter
from ccserver.model.info.registry import ModelInfoRegistry


class QwenPlugin(ProviderPlugin):
    """通义千问提供商插件。"""

    id = "qwen"
    name = "Qwen (通义千问)"
    transport_type = "openai-compat"

    # 默认 endpoint：中国区 dashscope
    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def create_adapter(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        **config: Any,
    ) -> ModelAdapter:
        """
        创建 OpenAIAdapter 连接通义千问。

        Args:
            api_key:  API 密钥，None 时使用 QWEN_API_KEY 环境变量
            base_url: API 端点，None 时使用默认中国区 endpoint
        """
        resolved_api_key = api_key or os.getenv("QWEN_API_KEY", "")
        resolved_base_url = base_url or self.DEFAULT_BASE_URL

        client = AsyncOpenAI(
            base_url=resolved_base_url,
            api_key=resolved_api_key,
            http_client=httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=600.0),
                limits=httpx.Limits(keepalive_expiry=5),
            ),
        )
        return OpenAIAdapter(client)

    def register_models(self, registry: ModelInfoRegistry) -> None:
        """注册通义千问系列模型。"""
        from ccserver.model.info.catalog import BUILTIN_QWEN_MODELS
        registry.register_bulk(BUILTIN_QWEN_MODELS)

    def create_media_provider(self):
        """
        创建并注册 Qwen 的 MediaUnderstandingProvider。

        auto_priority=15：低于 OpenAI(10)、高于 ZhipuAI(18)。
        使用 Qwen-VL 系列模型（如 qwen2.5-vl-72b-instruct）进行视觉理解。

        Returns:
            QwenMediaUnderstandingProvider 实例，或 None（如果 API key 不可用）
        """
        return _build_qwen_media_provider()


def _build_qwen_media_provider():
    """
    构建 Qwen 媒体理解提供者。

    auto_priority=15，使用 describe_image_with_model() 通用实现。
    默认使用 qwen-vl-max-latest 模型（阿里云 DashScope）。
    """
    import os
    from ccserver.model.media.base import MediaUnderstandingProvider
    from ccserver.model.media.describe import describe_image_with_model
    from ccserver.model.openai_adapter import OpenAIAdapter
    from openai import AsyncOpenAI
    import httpx

    DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    class QwenMediaUnderstandingProvider:
        provider_id = "qwen"
        auto_priority = 15

        def _get_adapter(self):
            """创建 OpenAIAdapter 连接 Qwen DashScope。"""
            api_key = os.getenv("QWEN_API_KEY", "")
            if not api_key:
                raise RuntimeError("QWEN_API_KEY not set")
            client = AsyncOpenAI(
                base_url=DEFAULT_QWEN_BASE_URL,
                api_key=api_key,
                http_client=httpx.AsyncClient(
                    timeout=httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=600.0),
                    limits=httpx.Limits(keepalive_expiry=5),
                ),
            )
            return OpenAIAdapter(client)

        async def describe_image(self, image_base64, prompt="", model=None, max_tokens=1000):
            adapter = self._get_adapter()
            resolved_model = model or "qwen-vl-max-latest"
            return await describe_image_with_model(
                image_base64=image_base64,
                prompt=prompt,
                adapter=adapter,
                model=resolved_model,
                max_tokens=max_tokens,
            )

        async def describe_images(self, images, prompt="", model=None, max_tokens=1000):
            if not images:
                return ""
            adapter = self._get_adapter()
            resolved_model = model or "qwen-vl-max-latest"
            results = []
            for img in images:
                desc = await describe_image_with_model(
                    image_base64=img.get("base64", ""),
                    prompt=prompt,
                    adapter=adapter,
                    model=resolved_model,
                    max_tokens=max_tokens,
                )
                results.append(desc)
            return "\n---\n".join(results)

        async def locate_element(self, image_base64, description, image_width, image_height, model=None):
            adapter = self._get_adapter()
            resolved_model = model or "qwen-vl-max-latest"

            locate_prompt = (
                f"请在 {image_width}x{image_height} 像素的截图"
                f"中定位：{description}。"
                f"返回格式：{{\"found\": true/false, \"x\": 像素x坐标, \"y\": 像素y坐标, \"confidence\": 0-1之间的置信度, "
                f"\"element_bounds\": \"描述元素大小和位置\"}}。"
                f"只返回 JSON，不要其他内容。"
            )
            result_text = await describe_image_with_model(
                image_base64=image_base64,
                prompt=locate_prompt,
                adapter=adapter,
                model=resolved_model,
                max_tokens=200,
            )
            import json
            try:
                return json.loads(result_text)
            except json.JSONDecodeError:
                import re
                match = re.search(r'\{[^}]+\}', result_text)
                if match:
                    try:
                        return json.loads(match.group())
                    except json.JSONDecodeError:
                        pass
                return {"found": False, "reason": "无法解析定位结果"}

    return QwenMediaUnderstandingProvider()
