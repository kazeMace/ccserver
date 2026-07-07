"""Primitive condition evaluator."""

from __future__ import annotations

from typing import Any, Callable

from drama_engine.core.dsl.components.conditions.keys import CONDITION_KEYS
from drama_engine.core.dsl.components.conditions.operators import compare_operator
from drama_engine.core.dsl.components.value_resolver import ValueResolver
from drama_engine.core.engine import State


class PrimitiveConditionEvaluator:
    """Evaluate deterministic, in-process DSL condition primitives."""

    def __init__(self, plugin_registry: Any = None, evaluate_condition: Callable | None = None):
        """
        Initialize the primitive evaluator.

        Args:
            plugin_registry: Optional plugin registry for value resolvers.
            evaluate_condition: Callback to evaluate nested condition dictionaries.
        """
        self._values = ValueResolver(plugin_registry)
        self._evaluate = evaluate_condition

    def evaluate_ref_condition(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """Evaluate the preferred ref/op/value condition form."""
        left = self.resolve_value_expr(
            {"ref": cond["ref"]},
            state=state,
            actor=actor,
            candidate=candidate,
            responses=responses,
            extra=extra,
            entity=entity,
            allow_entity_shorthand=False,
        )
        op = str(cond.get("op") or "equals")
        expected_spec = cond.get("value", cond.get("expected"))
        right = self.resolve_value_expr(
            expected_spec,
            state=state,
            actor=actor,
            candidate=candidate,
            responses=responses,
            extra=extra,
            entity=entity,
        )
        return compare_operator(left, op, right)

    def evaluate_compare_condition(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """Evaluate left/op/right with arbitrary value expressions."""
        left = self.resolve_value_expr(
            cond["left"],
            state=state,
            actor=actor,
            candidate=candidate,
            responses=responses,
            extra=extra,
            entity=entity,
            allow_entity_shorthand=entity is not None,
            prefer_ref=True,
        )
        op = str(cond.get("op") or "equals")
        right_spec = cond.get("right", cond.get("value", cond.get("expected")))
        right = self.resolve_value_expr(
            right_spec,
            state=state,
            actor=actor,
            candidate=candidate,
            responses=responses,
            extra=extra,
            entity=entity,
            prefer_ref=True,
        )
        return compare_operator(left, op, right)

    def evaluate_value_condition(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """Evaluate the legacy-compatible value/operator condition form."""
        value = self.resolve_value_expr(
            cond["value"],
            state=state,
            actor=actor,
            candidate=candidate,
            responses=responses,
            extra=extra,
            entity=entity,
            allow_entity_shorthand=entity is not None,
        )
        for operator in (
            "equal",
            "not_equal",
            "greater_than",
            "less_than",
            "greater_than_equal",
            "less_than_equal",
            "in",
            "not_in",
        ):
            if operator in cond:
                expected = self.resolve_value_expr(
                    cond[operator],
                    state,
                    actor,
                    candidate,
                    responses,
                    extra,
                    entity,
                )
                return compare_operator(value, operator, expected)
        if "is_null" in cond:
            return (value is None) == bool(cond["is_null"])
        if "not_null" in cond:
            return (value is not None) == bool(cond["not_null"])
        raise ValueError(f"value 条件缺少比较操作符: {cond}")

    def evaluate_state_condition(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None = None,
    ) -> bool:
        """Evaluate legacy state/operator conditions."""
        value = self.resolve_path(cond["state"], state, actor, candidate)
        if "equals" in cond:
            expected = cond["equals"]
            if isinstance(expected, bool) and value is None:
                value = False
            return value == expected
        if "not_equals" in cond:
            return value != cond["not_equals"]
        if "is_null" in cond:
            return (value is None) == cond["is_null"]
        if "not_null" in cond:
            return (value is not None) == cond["not_null"]
        if "gte" in cond:
            return value is not None and value >= cond["gte"]
        if "lte" in cond:
            return value is not None and value <= cond["lte"]
        if "gt" in cond:
            return value is not None and value > cond["gt"]
        if "lt" in cond:
            return value is not None and value < cond["lt"]
        if "in" in cond:
            return value in cond["in"]
        if "not_in" in cond:
            return value not in cond["not_in"]
        if "equals_state" in cond:
            other = self.resolve_path(cond["equals_state"], state, actor, candidate)
            return value == other
        if "not_equals_state" in cond:
            other = self.resolve_path(cond["not_equals_state"], state, actor, candidate)
            return value != other
        raise ValueError(f"未知 state 比较操作符: {cond}")

    def evaluate_count_condition(self, cond: dict, state: State) -> bool:
        """Evaluate legacy count/operator conditions."""
        count_spec = cond["count"]
        count = self.resolve_count(count_spec, state)
        if "equals" in cond:
            return count == cond["equals"]
        if "gte" in cond:
            return count >= cond["gte"]
        if "lte" in cond:
            return count <= cond["lte"]
        if "gt" in cond:
            return count > cond["gt"]
        if "lt" in cond:
            return count < cond["lt"]
        if "gte_than" in cond:
            other_count = self.resolve_count(cond["gte_than"]["count"], state)
            return count >= other_count
        raise ValueError(f"count 条件缺少比较操作符: {cond}")

    def evaluate_item_available(
        self,
        spec: dict,
        state: State,
        actor: str | None,
        candidate: str | None = None,
    ) -> bool:
        """Check whether an entity has a usable item."""
        entity = spec["entity"]
        if entity == "actor":
            assert actor is not None, "item_available 条件含 'actor' 但未传入 actor"
            entity = actor
        elif entity == "candidate":
            assert candidate is not None, "item_available 条件含 'candidate' 但未传入 candidate"
            entity = candidate
        item = spec["item"]
        attr_name = f"inventory_{item}"
        count = state.get_attr(entity, attr_name)
        if count is None:
            return False
        if count == "unlimited":
            return True
        return int(count) > 0

    def resolve_value_expr(
        self,
        expr: Any,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None = None,
        extra: dict | None = None,
        entity: str | None = None,
        allow_entity_shorthand: bool = False,
        prefer_ref: bool = False,
    ) -> Any:
        """Resolve a DSL value expression."""
        context = dict(extra or {})
        if entity is not None:
            context["entity"] = entity
        if isinstance(expr, dict) and "count" in expr:
            return self.resolve_count(expr["count"], state)
        if isinstance(expr, dict) and "ref" in expr:
            return self._values.resolve(
                expr,
                state=state,
                responses=responses,
                actor=actor,
                candidate=candidate,
                extra=context,
            )
        if prefer_ref and isinstance(expr, str) and self._looks_like_ref_string(expr):
            return self._values.resolve(
                {"ref": expr},
                state=state,
                responses=responses,
                actor=actor,
                candidate=candidate,
                extra=context,
            )
        if allow_entity_shorthand and isinstance(expr, str):
            return state.get_attr(entity, expr)
        return self._values.resolve(
            expr,
            state=state,
            responses=responses,
            actor=actor,
            candidate=candidate,
            extra=context,
        )

    def _looks_like_ref_string(self, value: str) -> bool:
        """Return whether a plain string should be treated as a ref in comparisons."""
        if value.startswith("@"):
            return False
        if value in {
            "actor",
            "candidate",
            "entity",
            "winner",
            "data",
            "responses",
            "selection_result",
            "item",
            "result",
        }:
            return True
        if "." in value:
            return True
        return ":" in value

    def resolve_count(self, count_spec: dict, state: State) -> int:
        """Count entities matching the given filter spec."""
        if "ref" in count_spec:
            source = self.resolve_value_expr(
                {"ref": count_spec["ref"]},
                state=state,
                actor=None,
                candidate=None,
                responses=None,
                extra=None,
                entity=None,
            )
            if source is None:
                entities = []
            elif isinstance(source, (list, tuple, set)):
                entities = [str(item) for item in source]
            else:
                entities = [str(source)]
            where_spec = count_spec.get("where") or count_spec.get("filter") or {}
            if not where_spec:
                return len([entity for entity in entities if state.has_entity(entity)])
            count = 0
            for entity in entities:
                if state.has_entity(entity) and self.entity_matches_filter(entity, where_spec, state):
                    count += 1
            return count

        filter_spec = count_spec.get("where") or count_spec.get("filter", {})
        count = 0
        for entity in state.all_entities():
            if entity == "GAME":
                continue
            if self.entity_matches_filter(entity, filter_spec, state):
                count += 1
        return count

    def entity_matches_filter(self, entity: str, filter_spec: dict, state: State) -> bool:
        """Return whether one entity matches a filter spec."""
        if self.looks_like_condition(filter_spec):
            assert self._evaluate is not None, "nested condition evaluator 不能为空"
            return self._evaluate(
                filter_spec,
                state=state,
                actor=None,
                candidate=None,
                entity=entity,
            )
        for attr, expected in filter_spec.items():
            actual = state.get_attr(entity, attr)
            if actual != expected:
                return False
        return True

    def looks_like_condition(self, spec: Any) -> bool:
        """Return whether a dict looks like a condition AST."""
        if not isinstance(spec, dict):
            return False
        return any(key in CONDITION_KEYS for key in spec)

    def filter_entities(self, filter_spec: dict, state: State) -> set:
        """Return entity names matching a filter spec."""
        source = filter_spec.get("source") if isinstance(filter_spec, dict) else None
        where_spec = filter_spec.get("where") if isinstance(filter_spec, dict) else None
        if source is not None or where_spec is not None:
            if source in {"GAME.players", "players"}:
                value = state.get_attr("GAME", "players") or []
                entities = [str(item) for item in value]
            elif isinstance(source, str) and "." in source:
                entity_name, attr_name = source.split(".", 1)
                value = state.get_attr(entity_name, attr_name) or []
                entities = [str(item) for item in value] if isinstance(value, (list, tuple, set)) else [str(value)]
            else:
                entities = [entity for entity in state.all_entities() if entity != "GAME"]
            return {
                entity
                for entity in entities
                if state.has_entity(entity) and self.entity_matches_filter(entity, where_spec or {}, state)
            }
        result = set()
        for entity in state.all_entities():
            if entity == "GAME":
                continue
            if self.entity_matches_filter(entity, filter_spec, state):
                result.add(entity)
        return result

    def resolve_path(
        self,
        path: str,
        state: State,
        actor: str | None,
        candidate: str | None = None,
    ) -> Any:
        """Resolve an entity.attr path from State."""
        if path == "actor":
            assert actor is not None, f"条件路径含 'actor' 但未传入 actor 参数: {path}"
            return actor
        if path == "candidate":
            assert candidate is not None, f"条件路径含 'candidate' 但未传入 candidate 参数: {path}"
            return candidate
        parts = path.split(".", 1)
        assert len(parts) == 2, f"state 路径必须是 'entity.attr' 格式，收到 '{path}'"
        entity, attr = parts
        if entity == "actor":
            assert actor is not None, f"条件路径含 'actor' 但未传入 actor 参数: {path}"
            entity = actor
        elif entity == "candidate":
            assert candidate is not None, f"条件路径含 'candidate' 但未传入 candidate 参数: {path}"
            entity = candidate
        return state.get_attr(entity, attr)


__all__ = ["PrimitiveConditionEvaluator"]
