from .adapter import ModelAdapter
from .anthropic_adapter import AnthropicAdapter, get_default_adapter, get_vlm_adapter
from .openai_adapter import OpenAIAdapter
from .volcano_adapter import VolcanoAdapter
from .zhipuai_adapter import ZhipuAIAdapter
from .factory import get_adapter

# Phase 1: 模型能力元数据
from .info import ModelInfo, ModelInfoRegistry, get_registry, BUILTIN_MODEL_CATALOG

# Phase 2: Provider Plugin 系统
from .plugins import ProviderPlugin, ProviderRegistry, get_provider_registry

# Phase 3: 媒体理解能力
from .media import (
    MediaUnderstandingProvider,
    MediaUnderstandingRegistry,
    get_media_registry,
    describe_image_with_model,
)

# Phase 4: VLM 路由
from .routing import VLMRouter, RouteResult, resolve_vlm_route, FallbackChain

# Phase 5: Transport
from .transport import TransportProtocol

__all__ = [
    # 核心抽象
    "ModelAdapter",
    # 具体实现
    "AnthropicAdapter",
    "OpenAIAdapter",
    "VolcanoAdapter",
    "ZhipuAIAdapter",
    # 工厂
    "get_adapter",
    "get_default_adapter",
    "get_vlm_adapter",
    # Phase 1: 模型能力元数据
    "ModelInfo",
    "ModelInfoRegistry",
    "get_registry",
    "BUILTIN_MODEL_CATALOG",
    # Phase 2: Provider Plugin
    "ProviderPlugin",
    "ProviderRegistry",
    "get_provider_registry",
    # Phase 3: 媒体理解
    "MediaUnderstandingProvider",
    "MediaUnderstandingRegistry",
    "get_media_registry",
    "describe_image_with_model",
    # Phase 4: VLM 路由
    "VLMRouter",
    "RouteResult",
    "resolve_vlm_route",
    "FallbackChain",
    # Phase 5: Transport
    "TransportProtocol",
]
