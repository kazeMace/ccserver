"""Script plugin registry for admin developer console.

第一版提供清晰的插件契约和一个示例插件，避免空壳页面。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class ScriptPluginInfo:
    """Metadata shown in admin plugin list."""

    plugin_id: str
    name: str
    description: str
    plugin_type: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class ScriptPlugin(Protocol):
    """Minimal script plugin protocol."""

    info: ScriptPluginInfo

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Run plugin and return JSON-friendly result."""


class ScriptPluginRegistry:
    """In-process registry for script development plugins."""

    def __init__(self) -> None:
        self._plugins: dict[str, ScriptPlugin] = {}
        self.register(EchoScriptPlugin())

    def register(self, plugin: ScriptPlugin) -> None:
        """Register one plugin."""
        assert plugin.info.plugin_id, "plugin_id 不能为空"
        self._plugins[plugin.info.plugin_id] = plugin

    def list_plugins(self) -> list[dict[str, str]]:
        """List plugin metadata."""
        return [plugin.info.to_dict() for plugin in self._plugins.values()]

    def run_plugin(self, plugin_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Run a registered plugin."""
        assert plugin_id, "plugin_id 不能为空"
        if plugin_id not in self._plugins:
            raise KeyError(f"插件不存在: {plugin_id}")
        return self._plugins[plugin_id].run(payload)


class EchoScriptPlugin:
    """Reference plugin that documents the plugin execution boundary."""

    info = ScriptPluginInfo(
        plugin_id="script_dev_echo",
        name="剧本开发示例插件",
        description="示例插件：回显输入，说明插件只能生成草稿建议，不能绕过 validate/inspect/playtest。",
        plugin_type="开发示例",
    )

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "plugin_id": self.info.plugin_id,
            "messages": [
                {"level": "info", "message": "插件已运行。真实插件应返回 DSL 草稿或检查建议。"},
                {"level": "info", "message": "插件输出必须保存为草稿，并继续检查、查看和试玩。"},
            ],
            "input": payload,
        }
