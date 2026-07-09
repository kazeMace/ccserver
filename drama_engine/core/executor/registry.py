"""执行器注册表。

Session 级持有，管理 4 种 executor 实例的注册与调用。
提供工厂函数 build_executor_registry() 在 session 创建时构建。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.executor.base import BaseExecutor, ExecutorRequest, ExecutorResponse

logger = logging.getLogger(__name__)

# executor 类型常量
EXECUTOR_LLM = "llm"
EXECUTOR_PLUGIN = "plugin"
EXECUTOR_HTTP = "http"
EXECUTOR_CODE = "code"
EXECUTOR_BUILTIN = "builtin"

# 底层传输 executor（非 builtin）
TRANSPORT_EXECUTORS = {EXECUTOR_LLM, EXECUTOR_PLUGIN, EXECUTOR_HTTP, EXECUTOR_CODE}


class ExecutorRegistry:
    """Session 级执行器注册表。

    每个 GameSession 创建时构建自己的 Registry 实例。
    LLMExecutor 持有该 session 的 metadata（Agent 缓存在其中）；
    PluginExecutor 持有该 session 的 plugin_registry。

    分派逻辑：
      - executor="builtin" 或省略 → 不走 executor，由上层功能组件注册表解析
      - executor="llm"/"plugin"/"http"/"code" → 走对应 executor
    """

    def __init__(self) -> None:
        """初始化空注册表。"""
        self._executors: dict[str, BaseExecutor] = {}

    def register(self, name: str, executor: BaseExecutor) -> None:
        """注册一个 executor 实例。

        参数:
            name: executor 类型名（llm/plugin/http/code）
            executor: BaseExecutor 实例
        """
        assert name, "executor name 不能为空"
        assert isinstance(executor, BaseExecutor), (
            f"executor 必须是 BaseExecutor 子类，收到 {type(executor)}"
        )
        self._executors[name] = executor
        logger.debug("[ExecutorRegistry] 注册 executor: %s", name)

    def get(self, name: str) -> BaseExecutor:
        """获取已注册的 executor。

        参数:
            name: executor 类型名

        返回:
            BaseExecutor 实例

        异常:
            KeyError: 未注册的 executor
        """
        executor = self._executors.get(name)
        if executor is None:
            raise KeyError(
                f"未注册的 executor: '{name}'。"
                f"已注册: {list(self._executors.keys())}"
            )
        return executor

    def has(self, name: str) -> bool:
        """检查是否注册了指定 executor。"""
        return name in self._executors

    async def execute(
        self,
        executor_name: str,
        request: ExecutorRequest,
    ) -> ExecutorResponse:
        """执行请求（便捷入口）。

        参数:
            executor_name: executor 类型名（llm/plugin/http/code）
            request: 统一请求

        返回:
            ExecutorResponse
        """
        executor = self.get(executor_name)
        return await executor.execute(request)

    def list_registered(self) -> list[str]:
        """列出所有已注册的 executor 名称。"""
        return list(self._executors.keys())


def build_executor_registry(
    session_metadata: dict[str, Any],
    plugin_registry: Any = None,
) -> ExecutorRegistry:
    """为一个 session 构建 ExecutorRegistry。

    在 session 创建时调用，挂到 ctx 上供全局使用。

    参数:
        session_metadata: session 级元数据（LLMExecutor 的 Agent 缓存在其中）
        plugin_registry: 插件注册表（PluginExecutor 需要）

    返回:
        构建好的 ExecutorRegistry 实例
    """
    from drama_engine.core.executor.code_executor import CodeExecutor
    from drama_engine.core.executor.http_executor import HttpExecutor
    from drama_engine.core.executor.llm_executor import LLMExecutor
    from drama_engine.core.executor.plugin_executor import PluginExecutor

    registry = ExecutorRegistry()

    # LLM executor（始终注册）
    registry.register(EXECUTOR_LLM, LLMExecutor(session_metadata))

    # Plugin executor（有 plugin_registry 时注册）
    if plugin_registry is not None:
        registry.register(EXECUTOR_PLUGIN, PluginExecutor(plugin_registry))

    # HTTP executor（始终注册）
    registry.register(EXECUTOR_HTTP, HttpExecutor())

    # Code executor（始终注册）
    registry.register(EXECUTOR_CODE, CodeExecutor())

    logger.info(
        "[ExecutorRegistry] 构建完成，已注册: %s",
        registry.list_registered(),
    )
    return registry


__all__ = [
    "ExecutorRegistry",
    "build_executor_registry",
    "EXECUTOR_LLM",
    "EXECUTOR_PLUGIN",
    "EXECUTOR_HTTP",
    "EXECUTOR_CODE",
    "EXECUTOR_BUILTIN",
    "TRANSPORT_EXECUTORS",
]
