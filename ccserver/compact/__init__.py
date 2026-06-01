"""
compact/__init__.py — compact 模块对外接口。

对外暴露的核心类型：
  Compactor         — 三层压缩协调器（agent.py 使用）
  CompactorFactory  — 一键构建默认 Compactor

Protocol（供自定义扩展使用）：
  MicroCompactor       — 轻量截断接口
  FullCompactor        — LLM 摘要压缩接口
  TriggerPolicy        — 触发策略接口
  SummarizationProvider — 摘要算法接口（原 CompactionProvider）

Default 实现（可直接使用或作为参考）：
  DefaultMicroCompactor
  DefaultFullCompactor
  DefaultTriggerPolicy
  CircuitBreaker

工具函数：
  estimate_tokens           — token 数量估算（修正图片虚高）
  strip_images_from_messages — 压缩前图片剥离
"""

from .compactor import Compactor, CompactorFactory
from .full import DefaultFullCompactor, FullCompactor, MemoryProvider, SummarizationProvider
from .micro import DefaultMicroCompactor, MicroCompactor
from .strip import strip_images_from_messages
from .tokens import IMAGE_TOKEN_SIZE, estimate_tokens
from .trigger import CircuitBreaker, DefaultTriggerPolicy, TriggerPolicy

__all__ = [
    # 核心
    "Compactor",
    "CompactorFactory",
    # Protocols
    "MicroCompactor",
    "FullCompactor",
    "TriggerPolicy",
    "SummarizationProvider",
    "MemoryProvider",
    # Default 实现
    "DefaultMicroCompactor",
    "DefaultFullCompactor",
    "DefaultTriggerPolicy",
    "CircuitBreaker",
    # 工具函数
    "estimate_tokens",
    "strip_images_from_messages",
    "IMAGE_TOKEN_SIZE",
]
