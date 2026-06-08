"""
base — MediaUnderstandingProvider 协议。

媒体理解能力提供者，与 ModelAdapter 解耦。
一个 provider 可以同时提供 LLM 对话能力和独立的媒体理解能力（图片描述、元素定位等）。

auto_priority 决定在多个提供者可用时优先使用哪个，数值越低优先级越高。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MediaUnderstandingProvider(Protocol):
    """
    媒体理解能力提供者协议。

    每个 provider（如 anthropic、qwen、zhipuai）可以独立注册此能力。
    系统按 auto_priority 排序自动选择最佳的 VLM provider。

    Attributes:
        provider_id:   关联的 provider id，如 "zhipuai"、"anthropic"
        auto_priority: 自动选择优先级（数值越低越优先）：
                       openai=10, qwen=15, zhipuai=18, anthropic=20, google=30, volcano=40
    """

    @property
    def provider_id(self) -> str:
        """关联的 provider id。"""
        ...

    @property
    def auto_priority(self) -> int:
        """自动选择优先级，数值越低越优先尝试。"""
        ...

    async def describe_image(
        self,
        image_base64: str,
        prompt: str = "",
        model: str | None = None,
        max_tokens: int = 1000,
    ) -> str:
        """
        对单张图片进行视觉描述，返回文字描述。

        Args:
            image_base64: PNG/JPEG 的 base64 编码数据
            prompt:       自定义描述引导词，为空时使用默认 prompt
            model:        指定模型名，None 时使用该 provider 的默认 VL 模型
            max_tokens:   输出最大 token 数

        Returns:
            图片的文本描述
        """
        ...

    async def describe_images(
        self,
        images: list[dict],
        prompt: str = "",
        model: str | None = None,
        max_tokens: int = 1000,
    ) -> str:
        """
        对多张图片进行视觉描述。

        Args:
            images:      图片列表，每项为 {"base64": str, "mime": str}
            prompt:       自定义描述引导词
            model:        指定模型名
            max_tokens:   输出最大 token 数

        Returns:
            图片的文本描述
        """
        ...

    async def locate_element(
        self,
        image_base64: str,
        description: str,
        image_width: int,
        image_height: int,
        model: str | None = None,
    ) -> dict:
        """
        在截图中定位指定元素，返回坐标信息。

        Args:
            image_base64: 截图的 base64 编码
            description:  目标元素的文字描述
            image_width:  图像宽度（像素）
            image_height: 图像高度（像素）
            model:        指定模型名

        Returns:
            {"found": bool, "x": int, "y": int, "confidence": float} 或
            {"found": False, "reason": str}
        """
        ...
