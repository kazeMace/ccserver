"""
info — 模型能力元数据包。

提供 ModelInfo 数据类（描述模型的输入能力）和 ModelInfoRegistry（全局注册表）。
"""

from .model_info import ModelInfo
from .registry import ModelInfoRegistry, get_registry
from .catalog import BUILTIN_MODEL_CATALOG

__all__ = [
    "ModelInfo",
    "ModelInfoRegistry",
    "get_registry",
    "BUILTIN_MODEL_CATALOG",
]
