"""
core — L0 抽象契约（已迁移至 providers/ 和 adapters/）。

adapter.py 和 stream.py 已删除，相关类已迁移到新架构：
  - ModelAdapter/LLMAdapter → ccserver.model_engine.providers.base.LLMProvider/BaseLLMProvider
  - StreamSession → ccserver.model_engine.providers.stream.ProviderStream

旧别名通过 aimodels/__init__.py 向后兼容导出（过渡期）。
Legacy aliases are re-exported via aimodels/__init__.py for transition period.
"""

__all__: list = []
