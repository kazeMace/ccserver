"""DSL validation issue models.

本模块只定义校验问题的数据结构。
This module only defines validation issue data structures.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(slots=True)
class ValidationIssue:
    """A single DSL validation issue.

    参数 / Args:
        level: fatal/error/warning/info.
        code: Stable machine-readable issue code.
        message: Human-readable problem description.
        path: Logical YAML/DSL path, such as ``flow.scenes[0].scope``.
        line: Optional 1-based source line number.
        column: Optional 1-based source column number.
        suggestion: Optional fix suggestion.
        source: Checker name.
    """

    level: str
    code: str
    message: str
    path: str = ""
    line: int | None = None
    column: int | None = None
    suggestion: str = ""
    source: str = "dsl_validator"

    def __post_init__(self) -> None:
        """Validate basic fields after construction."""
        assert self.level in {"fatal", "error", "warning", "info"}, f"invalid issue level: {self.level}"
        assert self.code, "code 不能为空"
        assert self.message, "message 不能为空"

    def to_dict(self) -> dict:
        """Return JSON-friendly issue dict."""
        return asdict(self)


class ValidationReport:
    """Collection of validation issues with summary helpers."""

    def __init__(self, issues: list[ValidationIssue] | None = None) -> None:
        self.issues = issues or []

    def add(self, issue: ValidationIssue) -> None:
        """Append one issue."""
        assert isinstance(issue, ValidationIssue), "issue 必须是 ValidationIssue"
        self.issues.append(issue)

    def extend(self, issues: list[ValidationIssue]) -> None:
        """Append many issues."""
        for issue in issues:
            self.add(issue)

    def summary(self) -> dict[str, int]:
        """Return issue counts grouped by level."""
        result = {"fatal": 0, "error": 0, "warning": 0, "info": 0}
        for issue in self.issues:
            result[issue.level] += 1
        return result

    def passed(self) -> bool:
        """Return True when no fatal/error issue exists."""
        summary = self.summary()
        return summary["fatal"] == 0 and summary["error"] == 0

    def to_dict(self) -> dict:
        """Return JSON-friendly report."""
        return {
            "summary": self.summary(),
            "passed": self.passed(),
            "issues": [issue.to_dict() for issue in self.issues],
        }
