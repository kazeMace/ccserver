"""Condition DSL key definitions."""

from __future__ import annotations

NEW_OPERATOR_KEYS = {
    "equal",
    "not_equal",
    "greater_than",
    "less_than",
    "greater_than_equal",
    "less_than_equal",
    "in",
    "not_in",
    "is_null",
    "not_null",
}

OLD_OPERATOR_KEYS = {
    "equals",
    "not_equals",
    "gte",
    "lte",
    "gt",
    "lt",
    "equals_state",
    "not_equals_state",
    "in",
    "not_in",
    "is_null",
    "not_null",
}

CONDITION_KEYS = NEW_OPERATOR_KEYS | OLD_OPERATOR_KEYS | {
    "all",
    "any",
    "not",
    # Preferred unified condition syntax.
    "executor",
    "id",
    "ref",
    "left",
    "op",
    "right",
    "expected",
    "pass_when",
    "fallback",
    "runtime",
    "language",
    "env",
    "code",
    "timeout_ms",
    "endpoint",
    "url",
    "input",
    "output_schema",
    "min_confidence",
    # Legacy condition syntax kept for old scripts.
    "state",
    "value",
    "count",
    "item_available",
    "python",
    "expr",
    "plugin",
}

__all__ = ["CONDITION_KEYS", "NEW_OPERATOR_KEYS", "OLD_OPERATOR_KEYS"]
