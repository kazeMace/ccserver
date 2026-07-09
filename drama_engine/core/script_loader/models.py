"""脚本加载统一数据对象。

所有消费方（Runner / Catalog / Compiler / API）都消费这些数据类。
ScriptBundle 是唯一的加载产物。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ScriptMeta:
    """脚本元数据 — 轻量身份信息。"""

    id: str
    name: str
    display_name: str = ""
    title: str = ""
    version: str = "1.0.0"
    author: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    locale: str = "zh-CN"
    recommended_player_role: str | None = None


@dataclass(slots=True)
class RawScriptDoc:
    """原始文档 — 合并后的 YAML 内容。"""

    doc: dict[str, Any]
    source_path: Path
    is_package: bool


@dataclass(slots=True)
class GamePackRef:
    """GamePack 引用。"""

    plugin_id: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PluginSpec:
    """插件声明。"""

    source: str  # "module" | "directory_file" | "inline"
    module_path: str | None = None  # Python module path（source=module）
    file_path: Path | None = None  # .py 文件路径（source=directory_file）
    register_func: str = "register"  # 注册函数名
    inline_spec: dict[str, Any] | None = None  # 内联声明（source=inline）


# 合法的 hook 事件名
VALID_HOOK_EVENTS = frozenset([
    "on_session_start",
    "on_session_end",
    "on_player_join",
    "on_game_over",
    "on_scene_enter",
    "on_scene_exit",
    "on_round_start",
    "on_round_end",
    "on_before_action",
    "on_after_action",
    "on_message",
    "on_referee_check",
])


@dataclass(slots=True)
class HookSpec:
    """Hook 声明。"""

    event: str  # 事件名，必须在 VALID_HOOK_EVENTS 中
    source: str  # "file" | "inline"
    file_path: Path | None = None  # hook 脚本路径（source=file）
    code: str | None = None  # 内联代码（source=inline）

    def __post_init__(self) -> None:
        assert self.event in VALID_HOOK_EVENTS, (
            f"未知 hook 事件: {self.event}，合法值: {sorted(VALID_HOOK_EVENTS)}"
        )


@dataclass(slots=True)
class ScriptBundle:
    """脚本加载的统一产物 — 所有消费方的唯一输入。

    从发现 → 读取 → 扫描 → 组装，全部结果汇聚于此。
    Compiler、Runner、Catalog、API 全部消费这一个对象。
    """

    # 身份
    bundle_id: str
    source_path: Path

    # 元数据
    meta: ScriptMeta

    # 原始内容
    raw_doc: dict[str, Any]
    roles: list[dict[str, Any]] = field(default_factory=list)

    # 扩展声明
    game_packs: list[GamePackRef] = field(default_factory=list)
    plugin_specs: list[PluginSpec] = field(default_factory=list)
    hook_specs: list[HookSpec] = field(default_factory=list)

    # 编译产物（惰性，首次访问时触发）
    _compiled: Any = field(default=None, repr=False)

    @property
    def compiled(self) -> Any:
        """惰性编译：首次访问触发 compile_doc。"""
        if self._compiled is None:
            from drama_engine.core.runtime.interactive_session.compiler import (
                InteractiveSessionCompiler,
            )
            self._compiled = InteractiveSessionCompiler().compile_doc(self.raw_doc)
        return self._compiled


__all__ = [
    "GamePackRef",
    "HookSpec",
    "PluginSpec",
    "RawScriptDoc",
    "ScriptBundle",
    "ScriptMeta",
    "VALID_HOOK_EVENTS",
]
