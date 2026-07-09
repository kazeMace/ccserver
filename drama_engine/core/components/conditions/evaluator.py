"""Composed condition evaluator entrypoint."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from drama_engine.core.components.conditions.code import CodeConditionEvaluator
from drama_engine.core.components.conditions.external import ExternalConditionEvaluator
from drama_engine.core.components.conditions.plugin import PluginConditionEvaluator
from drama_engine.core.components.conditions.primitive import PrimitiveConditionEvaluator
from drama_engine.core.engine import State


class ConditionEvaluator:
    """
    Evaluate DSL `when` dictionaries.

    This class is intentionally a small orchestrator. Concrete judgement
    methods live in composed evaluators:

    - PrimitiveConditionEvaluator: ref/left/value/count/state/item/filter.
    - CodeConditionEvaluator: executor: code and legacy python.
    - ExternalConditionEvaluator: executor: http / llm.
    - PluginConditionEvaluator: plugin registry delegation.
    """

    def __init__(self, plugin_registry: Any = None):
        """Initialize all condition evaluator components."""
        self._plugins = plugin_registry
        self._primitive = PrimitiveConditionEvaluator(
            plugin_registry=plugin_registry,
            evaluate_condition=self.evaluate,
        )
        self._code = CodeConditionEvaluator(self._primitive.entity_matches_filter)
        self._external = ExternalConditionEvaluator(
            evaluate_condition=self.evaluate,
            resolve_value_expr=self._primitive.resolve_value_expr,
        )
        self._plugin = PluginConditionEvaluator(plugin_registry)

    def evaluate(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None = None,
        responses: list | None = None,
        extra: dict | None = None,
        entity: str | None = None,
    ) -> bool:
        """
        Evaluate one condition dictionary.

        Args:
            cond: DSL condition dictionary.
            state: Current runtime state.
            actor: Current actor id, if any.
            candidate: Current candidate id, if any.
            responses: Current scene responses.
            extra: Runtime-specific context.
            entity: Current filter entity.

        Returns:
            True when the condition passes.
        """
        assert isinstance(cond, dict), f"条件必须是 dict，收到 {type(cond)}"

        if "all" in cond:
            return all(
                self.evaluate(item, state, actor, candidate, responses, extra, entity)
                for item in cond["all"]
            )
        if "any" in cond:
            return any(
                self.evaluate(item, state, actor, candidate, responses, extra, entity)
                for item in cond["any"]
            )
        if "not" in cond:
            return not self.evaluate(
                cond["not"],
                state,
                actor,
                candidate,
                responses,
                extra,
                entity,
            )

        if "executor" in cond:
            return self._evaluate_by_executor(
                cond,
                state,
                actor,
                candidate,
                responses,
                extra,
                entity,
            )
        if "left" in cond and "op" in cond:
            return self._primitive.evaluate_compare_condition(
                cond,
                state,
                actor,
                candidate,
                responses,
                extra,
                entity,
            )
        if "ref" in cond and "op" in cond:
            return self._primitive.evaluate_ref_condition(
                cond,
                state,
                actor,
                candidate,
                responses,
                extra,
                entity,
            )
        if "plugin" in cond:
            return self._plugin.evaluate(
                str(cond["plugin"]),
                cond,
                state,
                actor,
                candidate,
                responses,
                extra,
                entity,
            )
        if "value" in cond:
            return self._primitive.evaluate_value_condition(
                cond=cond,
                state=state,
                actor=actor,
                candidate=candidate,
                responses=responses,
                extra=extra,
                entity=entity,
            )
        if "count" in cond:
            return self._primitive.evaluate_count_condition(cond, state)
        if "item_available" in cond:
            return self._primitive.evaluate_item_available(
                cond["item_available"],
                state,
                actor,
                candidate,
            )
        if "python" in cond:
            return self._code.evaluate_python(cond["python"], state, actor, candidate)
        if "expr" in cond:
            logging.getLogger(__name__).warning(
                "条件原语 expr 尚未接入 LLM 求值，返回 default=%s: %s",
                cond.get("default", False),
                cond["expr"],
            )
            return bool(cond.get("default", False))
        if "state" in cond:
            return self._primitive.evaluate_state_condition(
                cond,
                state,
                actor,
                candidate,
            )
        raise ValueError(f"未知条件格式: {cond}")

    async def evaluate_async(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None = None,
        responses: list | None = None,
        extra: dict | None = None,
        entity: str | None = None,
    ) -> bool:
        """Async condition evaluation for interactive runtimes."""
        assert isinstance(cond, dict), f"条件必须是 dict，收到 {type(cond)}"
        if "all" in cond:
            for item in cond["all"]:
                if not await self.evaluate_async(item, state, actor, candidate, responses, extra, entity):
                    return False
            return True
        if "any" in cond:
            for item in cond["any"]:
                if await self.evaluate_async(item, state, actor, candidate, responses, extra, entity):
                    return True
            return False
        if "not" in cond:
            return not await self.evaluate_async(
                cond["not"],
                state,
                actor,
                candidate,
                responses,
                extra,
                entity,
            )
        if "executor" in cond:
            evaluator = str(cond.get("executor") or "builtin")
            if evaluator in {"http", "llm"}:
                return await self._external.evaluate_async(
                    cond,
                    state,
                    actor,
                    candidate,
                    responses,
                    extra,
                    entity,
                )
            if evaluator == "plugin":
                plugin_name = cond.get("plugin") or cond.get("name") or cond.get("id")
                if not plugin_name:
                    raise ValueError(f"plugin executor 缺少 name/id/plugin: {cond}")
                return await self._plugin.evaluate_async(
                    str(plugin_name),
                    cond,
                    state,
                    actor,
                    candidate,
                    responses,
                    extra,
                    entity,
                )
            if evaluator == "code":
                return await asyncio.to_thread(
                    self._code.evaluate,
                    cond,
                    state,
                    actor,
                    candidate,
                    responses,
                    extra,
                    entity,
                )
        if "plugin" in cond:
            return await self._plugin.evaluate_async(
                str(cond["plugin"]),
                cond,
                state,
                actor,
                candidate,
                responses,
                extra,
                entity,
            )
        return self.evaluate(cond, state, actor, candidate, responses, extra, entity)

    def _evaluate_by_executor(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """Dispatch an explicit executor condition."""
        evaluator = str(cond.get("executor") or "builtin")
        if evaluator in {"builtin", "primitive"}:
            if "left" in cond and "op" in cond:
                return self._primitive.evaluate_compare_condition(
                    cond,
                    state,
                    actor,
                    candidate,
                    responses,
                    extra,
                    entity,
                )
            if "ref" in cond and "op" in cond:
                return self._primitive.evaluate_ref_condition(
                    cond,
                    state,
                    actor,
                    candidate,
                    responses,
                    extra,
                    entity,
                )
            nested = cond.get("condition") or cond.get("when")
            if isinstance(nested, dict):
                return self.evaluate(
                    nested,
                    state,
                    actor,
                    candidate,
                    responses,
                    extra,
                    entity,
                )
            raise ValueError(f"builtin executor 缺少 left/op 或 condition: {cond}")
        if evaluator == "code":
            return self._code.evaluate(
                cond,
                state,
                actor,
                candidate,
                responses,
                extra,
                entity,
            )
        if evaluator in {"http", "llm"}:
            return self._external.evaluate(
                cond,
                state,
                actor,
                candidate,
                responses,
                extra,
                entity,
            )
        if evaluator == "plugin":
            plugin_name = cond.get("plugin") or cond.get("name") or cond.get("id")
            if not plugin_name:
                raise ValueError(f"plugin executor 缺少 name/id/plugin: {cond}")
            return self._plugin.evaluate(
                str(plugin_name),
                cond,
                state,
                actor,
                candidate,
                responses,
                extra,
                entity,
            )
        raise ValueError(f"未知 condition executor: {evaluator}")

    def filter_entities(self, filter_spec: dict, state: State) -> set:
        """Compatibility proxy for entity filtering."""
        return self._primitive.filter_entities(filter_spec, state)

    def _resolve_path(
        self,
        path: str,
        state: State,
        actor: str | None,
        candidate: str | None = None,
    ) -> Any:
        """Compatibility proxy for legacy compiler call sites."""
        return self._primitive.resolve_path(path, state, actor, candidate)


__all__ = ["ConditionEvaluator"]
