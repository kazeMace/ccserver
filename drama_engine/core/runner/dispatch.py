"""Runner dispatch for DSL runtime declarations.

本模块根据 DSL 的 runtime.type 创建对应 runner。
game_session 使用 SocialDeductionGameRunner；group_chat / dynamic_story 使用各自
execution model，防止不同 runtime 类型误走固定流程执行模型。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from drama_engine.core.runner.base import BasicGameRunner
from drama_engine.core.runtime_spec.registry import RuntimeSpec, build_default_runtime_registry


class UnsupportedRuntimeRunner(BasicGameRunner):
    """已识别但尚未实现的 Runtime runner。"""

    def __init__(self, runtime: Any, declaration: RuntimeSpec) -> None:
        """保存 Web session runtime 和 DSL runtime 声明。"""
        assert runtime is not None, "runtime 不能为空"
        assert declaration is not None, "declaration 不能为空"
        super().__init__(runtime=runtime, declaration=declaration)

    async def assign(self) -> None:
        """阻止未实现 runtime 进入发牌流程。"""
        raise NotImplementedError(self._message())

    async def start(self) -> None:
        """阻止未实现 runtime 启动。"""
        raise NotImplementedError(self._message())

    async def reset_runtime_state(self) -> None:
        """未实现 runtime 没有可重置的执行状态。"""
        return None

    def _message(self) -> str:
        """返回面向调用方的清晰错误。"""
        return (
            f"runtime.type '{self.declaration.type}' 已注册，"
            "但对应 Runner 尚未实现"
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

    if declaration.type == "game_session":
        runner_class = _fixed_flow_runner_class(script_path)

        return runner_class(runtime=runtime, declaration=declaration, dry_run=dry_run)
    if declaration.type == "group_chat":
        from drama_engine.core.execution_models.group_chat import GroupChatExecutionModel

        return GroupChatExecutionModel(runtime=runtime, declaration=declaration, dry_run=dry_run)
    if declaration.type == "dynamic_story":
        from drama_engine.core.execution_models.dynamic_story import DynamicStoryExecutionModel

        return DynamicStoryExecutionModel(runtime=runtime, declaration=declaration, dry_run=dry_run)
    return UnsupportedRuntimeRunner(runtime=runtime, declaration=declaration)


def _fixed_flow_runner_class(script_path: str) -> type[BasicGameRunner]:
    """Choose a fixed-flow runner specialization from domain declarations."""
    raw_text = Path(script_path).read_text(encoding="utf-8")
    doc = yaml.safe_load(raw_text) or {}
    assert isinstance(doc, dict), "script YAML 顶层必须是 dict"
    from drama_engine.core.execution_models.fixed_flow import (
        BoardGameRunner,
        CardGameRunner,
        EconomyGameRunner,
        SocialDeductionGameRunner,
    )

    extension_spec = doc.get("extensions") if isinstance(doc.get("extensions"), dict) else {}
    extension_keys = set(extension_spec.keys())
    scene_types = _scene_types(doc)

    if "cards" in doc or "cards" in extension_keys or "card" in scene_types:
        return CardGameRunner
    if "board" in doc or "board" in extension_keys or "board" in scene_types:
        return BoardGameRunner
    if "economy" in doc or "economy" in extension_keys:
        return EconomyGameRunner
    return SocialDeductionGameRunner


def _scene_types(doc: dict[str, Any]) -> set[str]:
    """Collect scene_type values from flow.scenes."""
    flow = doc.get("flow")
    if not isinstance(flow, dict):
        return set()
    scenes = flow.get("scenes")
    if not isinstance(scenes, list):
        return set()
    result = set()
    for scene in scenes:
        if isinstance(scene, dict) and scene.get("scene_type"):
            result.add(str(scene["scene_type"]))
    return result
