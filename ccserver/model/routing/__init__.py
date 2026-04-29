"""
routing — VLM 自动路由系统。

提供 VLMRouter（路由决策器）和 FallbackChain（失败重试链）。
实现 OpenClaw 的三层 VLM 路由：NATIVE → TRANSCRIBE → FALLBACK。
"""

from .router import VLMRouter, RouteResult, resolve_vlm_route
from .fallback import FallbackChain

__all__ = [
    "VLMRouter",
    "RouteResult",
    "resolve_vlm_route",
    "FallbackChain",
]
