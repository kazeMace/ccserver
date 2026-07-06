"""Plugin-backed condition evaluator."""

from __future__ import annotations

from typing import Any


class PluginConditionEvaluator:
    """Delegate plugin conditions to the configured plugin registry."""

    def __init__(self, plugin_registry: Any = None):
        """
        Initialize the plugin evaluator.

        Args:
            plugin_registry: Registry that provides evaluate_condition().
        """
        self._plugins = plugin_registry

    def evaluate(
        self,
        plugin_name: str,
        cond: dict,
        state: Any,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """Evaluate one plugin condition."""
        if self._plugins is None:
            raise ValueError(f"未配置插件注册表，无法求值 plugin condition: {cond}")
        return self._plugins.evaluate_condition(
            plugin_name,
            cond,
            {
                "state": state,
                "actor": actor,
                "candidate": candidate,
                "responses": responses or [],
                "extra": extra or {},
                "entity": entity,
            },
        )

    async def evaluate_async(
        self,
        plugin_name: str,
        cond: dict,
        state: Any,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """Evaluate one plugin condition from an async runtime path."""
        if self._plugins is None:
            raise ValueError(f"未配置插件注册表，无法求值 plugin condition: {cond}")
        context = {
            "state": state,
            "actor": actor,
            "candidate": candidate,
            "responses": responses or [],
            "extra": extra or {},
            "entity": entity,
        }
        if hasattr(self._plugins, "evaluate_condition_async"):
            return await self._plugins.evaluate_condition_async(plugin_name, cond, context)
        return self._plugins.evaluate_condition(plugin_name, cond, context)


__all__ = ["PluginConditionEvaluator"]
