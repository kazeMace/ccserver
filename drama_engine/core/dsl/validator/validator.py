"""Static DSL validator for Drama Engine scripts.

校验器用于管理开发端，负责在真正运行前发现 DSL 问题。当前系统只支持
`interactive_session` runtime，因此校验统一委托给 `InteractiveSessionCompiler`。
The validator now targets the single supported runtime (interactive_session) and
delegates structural/reference checks to InteractiveSessionCompiler.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from drama_engine.core.dsl.validator.issue import ValidationIssue, ValidationReport
from drama_engine.core.runtime.interactive_session.compiler import InteractiveSessionCompiler

logger = logging.getLogger(__name__)


class DslValidator:
    """Validate YAML syntax and interactive_session DSL compileability."""

    def __init__(self, compiler: InteractiveSessionCompiler | None = None) -> None:
        """初始化校验器。

        参数：
          compiler — interactive_session 编译器；默认新建一个。
        """
        self.compiler = compiler or InteractiveSessionCompiler()

    def validate_file(self, yaml_path: str | Path, params: dict[str, Any] | None = None) -> ValidationReport:
        """校验一个脚本文件。

        参数 / Args:
            yaml_path: Script YAML path.
            params: Optional compile params used by compiler checks.

        返回 / Returns:
            ValidationReport with fatal/error/warning/info issues.
        """
        path = Path(yaml_path)
        assert str(path), "yaml_path 不能为空"
        logger.info("[DslValidator] validate file: %s", path)
        try:
            raw_text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ValidationReport([
                ValidationIssue(
                    level="fatal",
                    code="SCRIPT_FILE_NOT_FOUND",
                    message=f"剧本文件不存在: {path}",
                    path=str(path),
                    suggestion="请确认 script_id 或上传文件路径是否正确。",
                    source="file_check",
                )
            ])
        return self.validate_text(raw_text, source_name=str(path), params=params)

    def validate_text(
        self,
        raw_text: str,
        source_name: str = "<uploaded>",
        params: dict[str, Any] | None = None,
    ) -> ValidationReport:
        """校验原始 YAML 文本。"""
        assert isinstance(raw_text, str), "raw_text 必须是字符串"
        report = ValidationReport()
        if not raw_text.strip():
            report.add(ValidationIssue(
                level="fatal",
                code="EMPTY_SCRIPT",
                message="剧本文本为空。",
                path=source_name,
                suggestion="请上传或输入有效的 YAML 剧本。",
                source="syntax_check",
            ))
            return report

        try:
            doc = yaml.safe_load(raw_text)
        except yaml.YAMLError as exc:
            mark = getattr(exc, "problem_mark", None)
            report.add(ValidationIssue(
                level="fatal",
                code="YAML_PARSE_ERROR",
                message=f"YAML 解析失败: {exc}",
                path=source_name,
                line=(mark.line + 1) if mark else None,
                column=(mark.column + 1) if mark else None,
                suggestion="请先修复 YAML 缩进、冒号、列表格式或引号问题。",
                source="syntax_check",
            ))
            return report

        if not isinstance(doc, dict):
            report.add(ValidationIssue(
                level="fatal",
                code="ROOT_NOT_OBJECT",
                message="DSL 根节点必须是对象/map。",
                path="$",
                suggestion="请确保 YAML 顶层包含 runtime、flow、scenes 等字段。",
                source="schema_check",
            ))
            return report

        doc = self._expand_param_templates(raw_text, doc, params, report, source_name)
        if not isinstance(doc, dict):
            return report

        report.extend(self._interactive_session_issues(doc))
        return report

    def _interactive_session_issues(self, doc: dict[str, Any]) -> list[ValidationIssue]:
        """用 interactive_session 编译器校验脚本结构与引用。"""
        issues: list[ValidationIssue] = []
        errors = self.compiler.validate(doc)
        for message in errors:
            issues.append(ValidationIssue(
                level="error",
                code="INTERACTIVE_SESSION_VALIDATE_ERROR",
                message=str(message),
                path="$",
                source="compile_check",
            ))
        return issues

    def _expand_param_templates(
        self,
        raw_text: str,
        doc: dict[str, Any],
        params: dict[str, Any] | None,
        report: ValidationReport,
        source_name: str,
    ) -> dict[str, Any]:
        """在结构校验前展开 ``{{param}}`` 模板。

        参数化脚本会在 players.count 等位置使用 ``{{total_players}}``。这里复用
        interactive_session 编译器的参数解析规则，确保 validate 与真实 compile 一致。
        """
        try:
            resolved = self.compiler._resolve_params(doc, params or {})
            if not resolved:
                return doc
            expanded_text = self.compiler._expand_params(raw_text, resolved)
            expanded_doc = yaml.safe_load(expanded_text) or {}
        except Exception as exc:  # noqa: BLE001 - report param expansion failures.
            report.add(ValidationIssue(
                level="error",
                code="PARAM_EXPANSION_ERROR",
                message=f"参数模板展开失败: {exc}",
                path=source_name,
                suggestion="请检查 params 默认值和 --param KEY=VALUE 覆盖值。",
                source="param_check",
            ))
            return doc
        if not isinstance(expanded_doc, dict):
            report.add(ValidationIssue(
                level="fatal",
                code="ROOT_NOT_OBJECT_AFTER_PARAMS",
                message="参数展开后 DSL 根节点必须是对象/map。",
                path="$",
                source="param_check",
            ))
            return {}
        return expanded_doc


__all__ = ["DslValidator", "ValidationIssue", "ValidationReport"]
