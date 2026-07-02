"""
metadata — 模型元数据包。

集中两类「关于模型/端点」的元数据，及各自的注册表与内置目录：
  - 能力维度 ModelInfo：模型能理解哪些输入（text/image/…）
  - 兼容维度 ModelCompatibility：endpoint 支持哪些协议特性（见 compatibility.py）
"""

from .model_info import ModelInfo
from .model_info_registry import ModelInfoRegistry, get_registry
from .model_info_catalog import BUILTIN_MODEL_CATALOG

__all__ = [
    "ModelInfo",
    "ModelInfoRegistry",
    "get_registry",
    "BUILTIN_MODEL_CATALOG",
]
