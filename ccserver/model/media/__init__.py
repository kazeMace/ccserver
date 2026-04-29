"""
media — 媒体理解能力包。

提供 MediaUnderstandingProvider 协议和 MediaUnderstandingRegistry，
将 VLM 能力与主 ModelAdapter 解耦。
"""

from .base import MediaUnderstandingProvider
from .registry import MediaUnderstandingRegistry, get_media_registry
from .describe import describe_image_with_model, DEFAULT_IMAGE_DESCRIPTION_PROMPT

__all__ = [
    "MediaUnderstandingProvider",
    "MediaUnderstandingRegistry",
    "get_media_registry",
    "describe_image_with_model",
    "DEFAULT_IMAGE_DESCRIPTION_PROMPT",
]
