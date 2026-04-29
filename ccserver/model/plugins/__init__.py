"""
plugins — ProviderPlugin 插件系统。

通过 import 插件类并注册到 ProviderRegistry，系统可以动态创建 ModelAdapter。
新增 provider 只需在此目录创建新的插件文件。
"""

from .base import ProviderPlugin
from .registry import ProviderRegistry, get_provider_registry

# 导入所有内置插件以便注册
from .anthropic import AnthropicPlugin
from .openai import OpenAIPlugin
from .volcano import VolcanoPlugin
from .qwen import QwenPlugin
from .zhipuai import ZhipuAIPlugin

__all__ = [
    "ProviderPlugin",
    "ProviderRegistry",
    "get_provider_registry",
    "AnthropicPlugin",
    "OpenAIPlugin",
    "VolcanoPlugin",
    "QwenPlugin",
    "ZhipuAIPlugin",
]
