"""
anthropic — Anthropic Provider Plugin。

包装现有 AnthropicAdapter，保持 get_default_adapter() 和 get_vlm_adapter() 行为不变。
注册 Claude 系列模型的 input_types 到 ModelInfoRegistry。
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from anthropic import AsyncAnthropic

from .base import ProviderPlugin
from ccserver.model.adapter import ModelAdapter
from ccserver.model.anthropic_adapter import AnthropicAdapter
from ccserver.model.info.registry import ModelInfoRegistry


class AnthropicPlugin(ProviderPlugin):
    """Anthropic 提供商插件。"""

    id = "anthropic"
    name = "Anthropic"
    transport_type = "anthropic"

    def create_adapter(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        **config: Any,
    ) -> ModelAdapter:
        """
        创建 AnthropicAdapter。

        Args:
            api_key:  API 密钥，None 时使用 ANTHROPIC_API_KEY 环境变量
            base_url: API 端点，None 时使用 ANTHROPIC_BASE_URL 环境变量
        """
        kwargs: dict[str, Any] = {
            "http_client": httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=600.0),
                limits=httpx.Limits(keepalive_expiry=5),
            ),
        }
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url

        client = AsyncAnthropic(**kwargs)
        return AnthropicAdapter(client)

    def register_models(self, registry: ModelInfoRegistry) -> None:
        """注册 Claude 系列模型。"""
        from ccserver.model.info.catalog import BUILTIN_ANTHROPIC_MODELS
        registry.register_bulk(BUILTIN_ANTHROPIC_MODELS)

    def create_media_provider(self):
        """
        创建并注册 Anthropic 的 MediaUnderstandingProvider。

        auto_priority=20：低于 Qwen(15)、高于 Google(30)。
        使用 Claude 原生视觉能力进行图像理解和元素定位。

        Returns:
            AnthropicMediaUnderstandingProvider 实例，或 None（如果 API key 不可用）
        """
        return _build_anthropic_media_provider()


def _build_anthropic_media_provider():
    """
    构建 Anthropic 媒体理解提供者。

    auto_priority=20，Claude 全系列支持 text+image，
    使用 describe_image_with_model() 通用实现。
    """
    import os
    from ccserver.model.media.base import MediaUnderstandingProvider
    from ccserver.model.media.describe import describe_image_with_model
    from ccserver.model.anthropic_adapter import AnthropicAdapter
    from anthropic import AsyncAnthropic

    class AnthropicMediaUnderstandingProvider:
        provider_id = "anthropic"
        auto_priority = 20

        def _get_adapter(self):
            """创建 AnthropicAdapter，优先使用 VLM 专用配置。"""
            from ccserver.config import VLM_API_KEY, VLM_BASE_URL
            import httpx

            kwargs = {
                "http_client": httpx.AsyncClient(
                    timeout=httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=600.0),
                    limits=httpx.Limits(keepalive_expiry=5),
                ),
            }
            api_key = VLM_API_KEY or os.getenv("ANTHROPIC_API_KEY", "")
            base_url = VLM_BASE_URL or os.getenv("ANTHROPIC_BASE_URL", "")
            if api_key:
                kwargs["api_key"] = api_key
            if base_url:
                kwargs["base_url"] = base_url
            if not kwargs.get("api_key"):
                raise RuntimeError("ANTHROPIC_API_KEY not set")
            return AnthropicAdapter(AsyncAnthropic(**kwargs))

        async def describe_image(self, image_base64, prompt="", model=None, max_tokens=1000):
            adapter = self._get_adapter()
            resolved_model = model or "claude-sonnet-4-6"
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
            resolved_model = model or "claude-sonnet-4-6"
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
            resolved_model = model or "claude-sonnet-4-6"

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

    return AnthropicMediaUnderstandingProvider()
