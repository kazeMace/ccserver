"""Default ccserver Agent factory for `provider: inside` services.

已迁移至 drama_engine.core.executor.agent_factory，此处仅 re-export 保持兼容。
"""

from drama_engine.core.executor.agent_factory import InsideAgentFactory

__all__ = ["InsideAgentFactory"]
