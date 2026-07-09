"""引擎级执行器（Executor）包。

提供 4 种通用传输能力：llm / plugin / http / code。
Executor 是纯传输层，不含业务逻辑。上层功能组件通过 ExecutorRegistry 调用。

使用方式:
    registry = build_executor_registry(session_metadata, plugin_registry)
    response = await registry.execute("llm", ExecutorRequest(...))
"""

from drama_engine.core.executor.base import (
    BaseExecutor,
    ExecutorRequest,
    ExecutorResponse,
)
from drama_engine.core.executor.registry import (
    ExecutorRegistry,
    build_executor_registry,
)

__all__ = [
    "BaseExecutor",
    "ExecutorRequest",
    "ExecutorResponse",
    "ExecutorRegistry",
    "build_executor_registry",
]
