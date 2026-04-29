"""
openai — OpenAI-compatible Provider Plugin。

一个类支持多个 OpenAI 兼容 endpoints：OpenAI、OpenRouter、Ollama、LMStudio、OneAPI、Generic。
通过构造函数参数区分 id/name/base_url，实现同一个类复用。

transport_type = "openai-compat"，使用现有的 OpenAIAdapter 实现。
"""

from __future__ import annotations

import os
from typing import Any

import httpx

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None  # type: ignore

from .base import ProviderPlugin
from ccserver.model.adapter import ModelAdapter
from ccserver.model.openai_adapter import OpenAIAdapter
from ccserver.model.info.registry import ModelInfoRegistry


class OpenAIPlugin(ProviderPlugin):
    """
    通用 OpenAI-compatible 提供商插件。

    通过构造函数参数化，一个类支持多种配置：
    - openai:     base_url=https://api.openai.com/v1,       env_api_key=OPENAI_API_KEY
    - openrouter: base_url=https://openrouter.ai/api/v1,      env_api_key=OPENROUTER_API_KEY
    - ollama:     base_url=http://localhost:11434/v1,         env_api_key=""
    - lmstudio:   base_url=http://localhost:1234/v1,          env_api_key=""
    - oneapi:     base_url_from_env(ONEAPI_BASE_URL),          env_api_key=ONEAPI_API_KEY
    - generic:    base_url="",                                 env_api_key=""
    """

    def __init__(
        self,
        provider_id: str,
        name: str,
        default_base_url: str,
        env_api_key: str,
    ):
        """
        初始化 OpenAI Plugin。

        Args:
            provider_id:      唯一标识，如 "openai"、"openrouter"、"ollama"
            name:             人类可读名称
            default_base_url: 默认 API 端点
            env_api_key:      环境变量名（如 "OPENAI_API_KEY"），空字符串表示不需要 API Key
        """
        self._id = provider_id
        self._name = name
        self._default_base_url = default_base_url
        self._env_api_key = env_api_key

    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def transport_type(self) -> str:
        return "openai-compat"

    def create_adapter(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        **config: Any,
    ) -> ModelAdapter:
        """
        创建 OpenAIAdapter。

        api_key 优先级：传入参数 > 环境变量 > 空字符串
        base_url 优先级：传入参数 > 默认值 > 空字符串
        """
        if AsyncOpenAI is None:
            raise ImportError("openai package is required for OpenAI-compatible providers. Install with: pip install openai")

        resolved_base_url = base_url if base_url is not None else self._default_base_url
        resolved_api_key = api_key

        if resolved_api_key is None and self._env_api_key:
            resolved_api_key = os.getenv(self._env_api_key, "")

        client = AsyncOpenAI(
            base_url=resolved_base_url or None,
            api_key=resolved_api_key or "",
            http_client=httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=600.0),
                limits=httpx.Limits(keepalive_expiry=5),
            ),
        )
        return OpenAIAdapter(client)

    def register_models(self, registry: ModelInfoRegistry) -> None:
        """根据 provider_id 注册对应模型。"""
        from ccserver.model.info.catalog import (
            BUILTIN_OPENAI_MODELS, BUILTIN_DEEPSEEK_MODELS,
        )

        # OpenAI 系列注册 GPT 模型
        if self._id == "openai":
            registry.register_bulk(BUILTIN_OPENAI_MODELS)

        # OpenRouter 有专属模型目录（注册 deepseek 模型，因为常通过 OpenRouter 调用）
        # 注意：deepseek 作为 provider 注册时也会注册这些模型
        # 这里跳过以避免重复，实际由 ProviderRegistry._init_defaults 按需注册

    def create_media_provider(self):
        """
        创建并注册 OpenAI 的 MediaUnderstandingProvider。

        仅 openai provider（非 openrouter/ollama 等）具有此能力。
        auto_priority=10：最高优先级，GPT-4o 系列视觉能力优秀。

        Returns:
            OpenAIMediaUnderstandingProvider 实例，或 None（非 openai provider 或 API key 不可用）
        """
        if self._id != "openai":
            return None
        return _build_openai_media_provider()


def _build_openai_media_provider():
    """
    构建 OpenAI 媒体理解提供者。

    auto_priority=10（最高优先级），GPT-4o 系列视觉理解能力强。
    使用 describe_image_with_model() 通用实现。
    """
    import os
    from ccserver.model.media.base import MediaUnderstandingProvider
    from ccserver.model.media.describe import describe_image_with_model
    from ccserver.model.openai_adapter import OpenAIAdapter
    from openai import AsyncOpenAI
    import httpx

    class OpenAIMediaUnderstandingProvider:
        provider_id = "openai"
        auto_priority = 10

        def _get_adapter(self):
            """创建 OpenAIAdapter。"""
            api_key = os.getenv("OPENAI_API_KEY", "")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY not set")
            client = AsyncOpenAI(
                base_url="https://api.openai.com/v1",
                api_key=api_key,
                http_client=httpx.AsyncClient(
                    timeout=httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=600.0),
                    limits=httpx.Limits(keepalive_expiry=5),
                ),
            )
            return OpenAIAdapter(client)

        async def describe_image(self, image_base64, prompt="", model=None, max_tokens=1000):
            adapter = self._get_adapter()
            resolved_model = model or "gpt-4o"
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
            resolved_model = model or "gpt-4o"
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
            resolved_model = model or "gpt-4o"

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

    return OpenAIMediaUnderstandingProvider()
