"""Plugin 执行器 — 调用用户在游戏包目录下定义的代码片段。

职责：从 plugin_registry 找到对应函数 → 传入 payload → 返回结果。
满足接口的 plugin 会被系统自动注入 ctx。

DSL 可配参数:
    executor: plugin
    name: "plugin_name"     # 必填，注册的插件名
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from drama_engine.core.executor.base import BaseExecutor, ExecutorRequest, ExecutorResponse

logger = logging.getLogger(__name__)


class PluginExecutor(BaseExecutor):
    """Plugin 执行器。

    通过 plugin_registry 调用用户定义的运行时服务。
    plugin_registry 是 session 级的，每个游戏包可以注册自己的 plugin。
    """

    def __init__(self, plugin_registry: Any) -> None:
        """初始化 Plugin 执行器。

        参数:
            plugin_registry: 插件注册表实例（需实现 has_runtime_service / call_runtime_service）
        """
        assert plugin_registry is not None, "PluginExecutor 需要 plugin_registry"
        self._registry = plugin_registry

    async def execute(self, request: ExecutorRequest) -> ExecutorResponse:
        """执行 Plugin 请求。

        request.config 必须包含 "name" 字段（插件名称）。
        request.payload 作为参数传递给 plugin。
        """
        name = request.config.get("name")
        assert name, "PluginExecutor 要求 config 中包含 name 字段"

        # 检查 plugin 是否已注册
        if not (hasattr(self._registry, "has_runtime_service")
                and self._registry.has_runtime_service(name)):
            logger.warning("[PluginExecutor] 未注册的 plugin: %s", name)
            return ExecutorResponse(
                success=False,
                error=f"未注册的 plugin: {name}",
            )

        # 调用 plugin
        try:
            result = self._registry.call_runtime_service(name, request.payload)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            logger.error("[PluginExecutor] plugin '%s' 调用失败: %s", name, exc)
            return ExecutorResponse(success=False, error=str(exc), raw=exc)

        # 规范化结果
        data = self._ensure_dict(result, name)
        logger.debug("[PluginExecutor] plugin '%s' 调用成功", name)
        return ExecutorResponse(success=True, data=data, raw=result)

    def _ensure_dict(self, result: Any, label: str) -> dict[str, Any]:
        """将 plugin 返回值规范化为 dict。"""
        if result is None:
            return {}
        if isinstance(result, dict):
            return result
        if isinstance(result, bool):
            return {"result": result}
        if isinstance(result, str):
            return {"text": result}
        logger.warning("[PluginExecutor] plugin '%s' 返回非 dict 类型: %s", label, type(result))
        return {"value": result}


__all__ = ["PluginExecutor"]
