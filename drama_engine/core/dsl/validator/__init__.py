"""DSL validation package.

DSL 校验包。负责静态检查 YAML/DSL 结构、引用、状态读写风险和编译可行性。
"""

from drama_engine.core.dsl.validator.issue import ValidationIssue, ValidationReport
from drama_engine.core.dsl.validator.validator import DslValidator

__all__ = ["DslValidator", "ValidationIssue", "ValidationReport"]
