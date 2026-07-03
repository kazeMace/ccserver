"""Runtime 类型声明与注册表。

设计目标：
- DSL 可以显式声明 runtime.type。
- 当前默认 runtime 是 game_session，保持现有狼人杀路径不变。
- game_session / group_chat / dynamic_story 都会经 runtime dispatch 接入
  对应 runner 与领域 runtime 组件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RuntimeSpec:
    """DSL 顶层 runtime 规格。

    参数：
      type   — Runtime 类型，例如 game_session / group_chat / dynamic_story。
      config — Runtime 配置字典；由具体 runtime 自己解释。
    """

    type: str = "game_session"
    config: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """保证 Runtime 声明处于可用状态。"""
        assert isinstance(self.type, str) and self.type.strip(), "runtime.type 必须是非空字符串"
        assert isinstance(self.config, dict), "runtime.config 必须是字典"
        self.type = self.type.strip()

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化字典。"""
        return {"type": self.type, "config": dict(self.config)}


class RuntimeRegistry:
    """Runtime 类型注册表。

    负责声明和校验 runtime.type；runner factory 由 runner.dispatch 选择。
    """

    def __init__(self) -> None:
        """初始化空注册表。"""
        self._types: dict[str, dict[str, Any]] = {}

    def register(self, name: str, description: str = "") -> None:
        """注册一个 runtime 类型。

        参数：
          name        — runtime 类型名。
          description — 面向文档和 UGC Skill 的说明。
        """
        assert isinstance(name, str) and name.strip(), "runtime 类型名必须是非空字符串"
        key = name.strip()
        self._types[key] = {"name": key, "description": description or ""}

    def has(self, name: str) -> bool:
        """检查 runtime 类型是否已注册。"""
        return isinstance(name, str) and name.strip() in self._types

    def names(self) -> list[str]:
        """返回已注册 runtime 类型名。"""
        return sorted(self._types.keys())

    def describe(self, name: str) -> dict[str, Any]:
        """返回 runtime 类型说明。"""
        assert self.has(name), f"runtime 类型未注册: {name}"
        return dict(self._types[name.strip()])

    def parse_declaration(self, spec: Any) -> RuntimeSpec:
        """解析 DSL runtime 规格。

        支持：
          - None：默认 game_session。
          - 字符串：runtime 类型。
          - 字典：{type: ..., config: ...}。
        """
        if spec is None:
            declaration = RuntimeSpec()
        elif isinstance(spec, str):
            declaration = RuntimeSpec(type=spec)
        elif isinstance(spec, dict):
            runtime_type = spec.get("type", "game_session")
            config = spec.get("config", {}) or {}
            declaration = RuntimeSpec(type=runtime_type, config=config)
        else:
            raise ValueError("runtime 必须是字典、字符串或空值")

        if not self.has(declaration.type):
            raise ValueError(
                f"未知 runtime.type: {declaration.type}；合法值：{self.names()}"
            )
        return declaration


def build_default_runtime_registry() -> RuntimeRegistry:
    """构建默认 Runtime 注册表。"""
    registry = RuntimeRegistry()
    registry.register("game_session", "固定剧本/派对/桌游流程 Runtime")
    registry.register("group_chat", "多 Agent 群聊互动 Runtime")
    registry.register("dynamic_story", "用户驱动动态剧情 Runtime")
    registry.register("interactive_session", "统一互动场景 Runtime，支持多 Agent 玩法与动态剧情")
    return registry
