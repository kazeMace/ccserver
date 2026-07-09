"""策略解析器 — 根据 executor 配置解析出 FreeInputStrategy 实例。

单一职责：把 DSL 中的 executor 配置（builtin/plugin/llm/http）
转化为可执行的策略对象。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.adapters import (
    HttpStrategyAdapter,
    LLMStrategyAdapter,
    PluginStrategyAdapter,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.registry import (
    FreeInputStrategyRegistry,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.strategies.base import (
    FreeInputStrategy,
)
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext

logger = logging.getLogger(__name__)


class StrategyResolver:
    """策略解析器。

    根据 executor 类型（builtin/plugin/llm/http）返回对应的策略实例。

    使用方式:
        resolver = StrategyResolver(strategy_registry, plugin_registry, llm_client)
        strategy = await resolver.resolve("choose_mapping", mapper_spec, ctx)
    """

    def __init__(
        self,
        strategy_registry: FreeInputStrategyRegistry | None = None,
        plugin_registry=None,
        llm_client=None,
    ) -> None:
        """初始化策略解析器。

        参数:
            strategy_registry: 内置策略注册表
            plugin_registry: 插件注册表（plugin executor 需要）
            llm_client: LLM 客户端（llm executor 需要）
        """
        self._registry = strategy_registry or FreeInputStrategyRegistry()
        self._plugin_registry = plugin_registry
        self._llm_client = llm_client

    async def resolve(
        self,
        mode: str,
        strategy_spec: dict[str, Any] | None,
        ctx: InteractiveExecutionContext,
    ) -> FreeInputStrategy:
        """解析并返回策略实例。

        参数:
            mode: 策略模式名称（choose_mapping/branch_then_return/...）
            strategy_spec: DSL 策略配置（mapper/generator 块）
            ctx: 运行时上下文（用于获取 plugin_registry）

        返回:
            策略实例

        异常:
            ValueError: 未知 executor 或缺少必要配置
        """
        executor = self._resolve_executor(strategy_spec, mode)

        if executor == "builtin":
            strategy = self._registry.get(mode)
            if strategy is None:
                raise ValueError(f"未注册内置策略: {mode}")
            return strategy

        elif executor == "plugin":
            registry = self._plugin_registry or ctx.plugin_registry
            assert registry is not None, "plugin adapter 需要 plugin_registry"
            plugin_name = strategy_spec.get("name")
            if not plugin_name:
                raise ValueError("plugin executor 需要指定 name")
            return PluginStrategyAdapter(registry, plugin_name, fallback_mode=mode)

        elif executor == "llm":
            assert self._llm_client is not None, "llm adapter 需要 llm_client"
            return LLMStrategyAdapter(
                mode=mode,
                llm_client=self._llm_client,
                spec=strategy_spec or {},
            )

        elif executor == "http":
            return HttpStrategyAdapter(
                mode=mode,
                spec=strategy_spec or {},
            )

        else:
            raise ValueError(f"未知 executor: {executor}")

    def _resolve_executor(self, strategy_spec: dict[str, Any] | None, mode: str = "") -> str:
        """解析执行引擎类型。

        参数:
            strategy_spec: DSL 策略配置
            mode: 策略模式名称（grow_flow 强制走 builtin）

        返回:
            执行引擎类型（builtin/plugin/llm/http）
        """
        # grow_flow 始终走 builtin（内部通过 Pipeline 处理 executor）
        if mode == "grow_flow":
            return "builtin"

        if not strategy_spec:
            return "builtin"

        executor = strategy_spec.get("executor")
        if executor in {"builtin", "plugin", "llm", "http"}:
            return executor

        return "builtin"


__all__ = ["StrategyResolver"]
