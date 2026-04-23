from __future__ import annotations

from pathlib import Path
from typing import Any

from ccserver.builtins.tools import BuiltinTools


class ToolManager:
    """
    负责过滤和查询所有可用工具。

    职责：
      1. 持有由 PromptLib.build_tools() 构建的工具字典
      2. 提供 register_custom_tool() 接口供其他模块注入外置工具
      3. 根据 ProjectSettings 的策略计算启用/禁用工具集
      4. 为 factory 层提供可直接绑定到 Agent 的 tools dict
    """

    def __init__(
        self,
        workdir: Path,
        task_manager: Any,
        settings: Any,
        tools: dict[str, BuiltinTools] | None = None,
    ):
        self.workdir = workdir
        self.task_manager = task_manager
        self.settings = settings
        self._builtin_tools: dict[str, BuiltinTools] = dict(tools) if tools else {}
        self._custom_tools: dict[str, BuiltinTools] = {}

    # ── 自定义工具注册 ──────────────────────────────────────────────────────────

    def register_custom_tool(self, tool: BuiltinTools) -> None:
        """注册外部/自定义工具。同名工具会覆盖内置版本（但不建议）。"""
        self._custom_tools[tool.name] = tool

    # ── 查询 ───────────────────────────────────────────────────────────────────

    def get_all_tools(self) -> dict[str, BuiltinTools]:
        """返回内置 + 自定义工具的合并字典。"""
        return {**self._builtin_tools, **self._custom_tools}

    def get_enabled_tools(self) -> tuple[dict[str, BuiltinTools], dict[str, BuiltinTools]]:
        """
        根据 settings 过滤生成启用/禁用工具集。

        Returns:
            (enabled_tools, disabled_tools)
        """
        all_tools = self.get_all_tools()
        enabled = self.settings.filter_tools(all_tools)
        disabled = {k: v for k, v in all_tools.items() if k not in enabled}
        return enabled, disabled

    def get_tool(self, name: str) -> BuiltinTools | None:
        """按名称获取工具实例（先查自定义，再查内置）。"""
        return self._custom_tools.get(name) or self._builtin_tools.get(name)
