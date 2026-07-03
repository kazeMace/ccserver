"""Comparison operators shared by condition evaluators."""

from __future__ import annotations

from typing import Any


def compare_operator(left: Any, op: str, right: Any = None) -> bool:
    """
    Compare two values with the unified DSL operator vocabulary.

    Args:
        left: Left value.
        op: Operator name from the DSL.
        right: Right value. Null checks can omit it.

    Returns:
        True when the comparison passes.

    Raises:
        ValueError: If the operator is unknown.
    """
    normalized = {
        "equals": "equal",
        "eq": "equal",
        "not_equals": "not_equal",
        "ne": "not_equal",
        "gte": "greater_than_equal",
        "lte": "less_than_equal",
        "gt": "greater_than",
        "lt": "less_than",
    }.get(op, op)
    if normalized == "equal":
        if isinstance(right, bool) and left is None:
            left = False
        return left == right
    if normalized == "not_equal":
        return left != right
    if normalized == "greater_than":
        return left is not None and left > right
    if normalized == "less_than":
        return left is not None and left < right
    if normalized == "greater_than_equal":
        return left is not None and left >= right
    if normalized == "less_than_equal":
        return left is not None and left <= right
    if normalized == "in":
        return left in right
    if normalized == "not_in":
        return left not in right
    if normalized == "is_null":
        return (left is None) == bool(right)
    if normalized == "not_null":
        return (left is not None) == bool(True if right is None else right)
    raise ValueError(f"未知比较操作符: {op}")


__all__ = ["compare_operator"]
