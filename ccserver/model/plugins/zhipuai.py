"""
zhipuai — 智谱 GLM Provider Plugin。

使用 zai-sdk（ZhipuAiClient），需要 pip install zai-sdk。
注册 GLM-5V 多模态系列和 GLM-4.5 文本系列模型。

特点：
- 原生支持 thinking={"type": "enabled"} 推理链
- image_url/video_url/file_url 三种多模态内容类型
- 独立 SDK，不与 OpenAIAdapter 复用
- 同时注册 MediaUnderstandingProvider（图片+视频+文件理解）
"""

from __future__ import annotations

import os
from typing import Any

from .base import ProviderPlugin
from ccserver.model.adapter import ModelAdapter
from ccserver.model.zhipuai_adapter import ZhipuAIAdapter
from ccserver.model.info.registry import ModelInfoRegistry


class ZhipuAIPlugin(ProviderPlugin):
    """智谱 GLM 提供商插件。"""

    id = "zhipuai"
    name = "ZhipuAI GLM (智谱)"
    transport_type = "zhipuai"

    # GLM 默认 VLM 模型（用于图像/视频理解）
    DEFAULT_VLM_MODEL = "glm-5v-turbo"

    def create_adapter(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        **config: Any,
    ) -> ModelAdapter:
        """
        创建 ZhipuAIAdapter。

        Args:
            api_key:  API 密钥，None 时使用 ZHIPUAI_API_KEY 环境变量
            base_url: zai-sdk 通常不需要手动指定，保留参数兼容
        """
        resolved_api_key = api_key or os.getenv("ZHIPUAI_API_KEY", "")
        if not resolved_api_key:
            raise ValueError(
                "ZhipuAI API key is required. "
                "Set ZHIPUAI_API_KEY environment variable or pass api_key parameter."
            )
        return ZhipuAIAdapter(api_key=resolved_api_key, base_url=base_url)

    def register_models(self, registry: ModelInfoRegistry) -> None:
        """注册 GLM 系列模型。"""
        from ccserver.model.info.catalog import BUILTIN_ZHIPUAI_MODELS
        registry.register_bulk(BUILTIN_ZHIPUAI_MODELS)

    def create_media_provider(self):
        """
        创建并注册 GLM 的 MediaUnderstandingProvider。

        在 ProviderRegistry._init_defaults() 中调用，
        将 GLM-5V 的能力注册到 MediaUnderstandingRegistry。

        Returns:
            GLMMediaUnderstandingProvider 实例，或 None（如果 SDK 不可用）
        """
        return _build_glm_media_provider(self)


def _build_glm_media_provider(plugin: ZhipuAIPlugin):
    """
    构建 GLM 媒体理解提供者。

    auto_priority=18：低于 Qwen(15) 高于 Anthropic(20)。

    使用 describe_image_with_model() 通用实现，
    GLM-5V 支持 image + video + file 多模态。
    """
    from ccserver.model.media.base import MediaUnderstandingProvider
    from ccserver.model.media.describe import describe_image_with_model

    class GLMMediaUnderstandingProvider:
        provider_id = "zhipuai"
        auto_priority = 18

        async def describe_image(self, image_base64, prompt="", model=None, max_tokens=1000):
            # 尝试注册 GLM 适配器
            api_key = os.getenv("ZHIPUAI_API_KEY", "")
            if not api_key:
                raise RuntimeError("ZHIPUAI_API_KEY not set")
            adapter = ZhipuAIAdapter(api_key=api_key)
            resolved_model = model or plugin.DEFAULT_VLM_MODEL
            return await describe_image_with_model(
                image_base64=image_base64,
                prompt=prompt,
                adapter=adapter,
                model=resolved_model,
                max_tokens=max_tokens,
            )

        async def describe_images(self, images, prompt="", model=None, max_tokens=1000):
            # 多图理解：将第一张图作为主图，其余拼接描述
            if not images:
                return ""
            api_key = os.getenv("ZHIPUAI_API_KEY", "")
            if not api_key:
                raise RuntimeError("ZHIPUAI_API_KEY not set")
            adapter = ZhipuAIAdapter(api_key=api_key)
            resolved_model = model or plugin.DEFAULT_VLM_MODEL
            # 对每张图分别描述，然后拼接
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
            api_key = os.getenv("ZHIPUAI_API_KEY", "")
            if not api_key:
                raise RuntimeError("ZHIPUAI_API_KEY not set")
            adapter = ZhipuAIAdapter(api_key=api_key)
            resolved_model = model or plugin.DEFAULT_VLM_MODEL

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
            # 解析 JSON 结果
            import json
            try:
                return json.loads(result_text)
            except json.JSONDecodeError:
                # 从文本中提取 JSON
                import re
                match = re.search(r'\{[^}]+\}', result_text)
                if match:
                    try:
                        return json.loads(match.group())
                    except json.JSONDecodeError:
                        pass
                return {"found": False, "reason": "无法解析定位结果"}

    return GLMMediaUnderstandingProvider()
