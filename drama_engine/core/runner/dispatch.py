"""Runner dispatch for DSL runtime declarations.

本模块根据 DSL 的 runtime.type 创建对应 runner。当前系统只支持
`interactive_session` 一种 runtime；其余 runtime.type 会被拒绝。
This module builds a runner from the DSL runtime.type. Only `interactive_session`
is supported now; any other runtime.type is rejected.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from drama_engine.core.runner.base import BasicGameRunner
from drama_engine.core.runtime_spec.registry import RuntimeSpec, build_default_runtime_registry


class UnsupportedRuntimeRunner(BasicGameRunner):
    """已识别但不受支持的 Runtime runner。"""

    def __init__(self, runtime: Any, declaration: RuntimeSpec) -> None:
        """保存 Web session runtime 和 DSL runtime 声明。"""
        assert runtime is not None, "runtime 不能为空"
        assert declaration is not None, "declaration 不能为空"
        super().__init__(runtime=runtime, declaration=declaration)

    async def assign(self) -> None:
        """阻止不受支持 runtime 进入发牌流程。"""
        raise NotImplementedError(self._message())

    async def start(self) -> None:
        """阻止不受支持 runtime 启动。"""
        raise NotImplementedError(self._message())

    async def reset_runtime_state(self) -> None:
        """不受支持 runtime 没有可重置的执行状态。"""
        return None

    def _message(self) -> str:
        """返回面向调用方的清晰错误。"""
        return (
            f"runtime.type '{self.declaration.type}' 不受支持；"
            "当前只支持 interactive_session"
        )


def read_runtime_declaration(script_path: str, params: dict[str, Any] | None = None) -> RuntimeSpec:
    """从 YAML 文件读取 runtime 声明。

    只读取顶层 runtime，不编译完整 Script，避免 session 创建阶段就触发完整编译。
    params 当前保留给后续 runtime 声明参数化使用。
    """
    assert script_path, "script_path 不能为空"
    _ = params or {}
    raw_text = Path(script_path).read_text(encoding="utf-8")
    doc = yaml.safe_load(raw_text) or {}
    registry = build_default_runtime_registry()
    return registry.parse_declaration(doc.get("runtime"))


def build_runner_for_session(runtime: Any, dry_run: bool = True) -> BasicGameRunner:
    """根据 session.script_path 的 runtime.type 创建 runner。"""
    assert runtime is not None, "runtime 不能为空"
    script_path = runtime.session.script_path
    declaration = read_runtime_declaration(script_path, runtime.session.params)
    runtime.session.metadata["runtime_type"] = declaration.type

    if declaration.type == "interactive_session":
        from drama_engine.core.runtime.interactive_session import InteractiveSessionExecutionModel

        return InteractiveSessionExecutionModel(runtime=runtime, declaration=declaration, dry_run=dry_run)
    return UnsupportedRuntimeRunner(runtime=runtime, declaration=declaration)
