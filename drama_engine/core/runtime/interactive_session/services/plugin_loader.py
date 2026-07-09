"""Load script-declared plugins for interactive_session."""

from __future__ import annotations

import importlib
import importlib.util
import logging
from pathlib import Path
from typing import Any

from drama_engine.core.dsl.plugins import PluginApi, PluginRegistry
from drama_engine.core.script_loader.models import PluginSpec

logger = logging.getLogger(__name__)


class InteractivePluginLoader:
    """Load plugin declarations into a PluginRegistry."""

    def load(self, registry: PluginRegistry, plugin_specs: list[dict[str, Any]]) -> None:
        """Load all plugin specs declared by the script (旧格式 dict 列表)。

        Supported forms:
        - {module: "pkg.mod", register: "register"}
        - {module: "pkg.mod", factory: "create_plugin"}
        - {runtime_services: {name: {result: ...}}}
        """
        assert registry is not None, "registry 不能为空"
        for spec in plugin_specs or []:
            if not isinstance(spec, dict):
                continue
            self._load_inline_services(registry, spec)
            self._load_inline_conditions(registry, spec)
            module_name = spec.get("module")
            if not module_name:
                continue
            module = importlib.import_module(str(module_name))
            if spec.get("factory"):
                plugin = getattr(module, str(spec["factory"]))()
                self._register_plugin_object(registry, plugin)
                continue
            register_name = str(spec.get("register") or "register")
            register = getattr(module, register_name)
            register(PluginApi(registry))

    def load_from_specs(self, registry: PluginRegistry, specs: list[PluginSpec]) -> None:
        """从 ScriptLoader 扫描的 PluginSpec 列表加载插件。

        支持的 source 类型：
        - "module": 通过 Python module path import
        - "directory_file": 从 .py 文件路径动态加载
        - "inline": 内联声明（暂不支持）
        """
        assert registry is not None, "registry 不能为空"
        api = PluginApi(registry)
        for spec in specs or []:
            try:
                if spec.source == "module" and spec.module_path:
                    module = importlib.import_module(spec.module_path)
                    register_fn = getattr(module, spec.register_func)
                    register_fn(api)
                    logger.info("[PluginLoader] 加载模块插件: %s", spec.module_path)

                elif spec.source == "directory_file" and spec.file_path:
                    self._load_from_file(api, spec.file_path, spec.register_func)

                elif spec.source == "inline" and spec.inline_spec:
                    self._load_inline_services(registry, spec.inline_spec)
                    self._load_inline_conditions(registry, spec.inline_spec)
                    logger.info("[PluginLoader] 加载内联插件")
                else:
                    logger.warning("[PluginLoader] 跳过未知 source: %s", spec.source)
            except Exception as e:
                logger.error("[PluginLoader] 加载插件失败: %s, 错误: %s", spec, e)
                raise

    def _load_from_file(self, api: PluginApi, file_path: Path, register_func: str) -> None:
        """从 .py 文件动态加载插件模块并调用 register 函数。"""
        module_name = f"_drama_plugin_{file_path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        assert spec is not None, f"无法创建模块 spec: {file_path}"
        assert spec.loader is not None, f"模块 spec 无 loader: {file_path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        register_fn = getattr(module, register_func, None)
        assert register_fn is not None, (
            f"插件文件 {file_path} 缺少 {register_func} 函数"
        )
        register_fn(api)
        logger.info("[PluginLoader] 加载文件插件: %s", file_path.name)

    def _load_inline_services(self, registry: PluginRegistry, spec: dict[str, Any]) -> None:
        """Load inline runtime service declarations."""
        services = spec.get("runtime_services") or {}
        if isinstance(services, list):
            services = {
                str(item.get("name")): item
                for item in services
                if isinstance(item, dict) and item.get("name")
            }
        if not isinstance(services, dict):
            return
        for name, service_spec in services.items():
            if not isinstance(service_spec, dict):
                continue
            registry.register_runtime_service(
                str(name),
                self._inline_service_handler(dict(service_spec)),
            )

    def _load_inline_conditions(self, registry: PluginRegistry, spec: dict[str, Any]) -> None:
        """Load inline plugin condition declarations."""
        conditions = spec.get("conditions") or {}
        if isinstance(conditions, list):
            conditions = {
                str(item.get("name")): item
                for item in conditions
                if isinstance(item, dict) and item.get("name")
            }
        if not isinstance(conditions, dict):
            return
        for name, condition_spec in conditions.items():
            if not isinstance(condition_spec, dict):
                continue
            registry.register_condition(
                str(name),
                self._inline_condition_handler(dict(condition_spec)),
            )

    def _inline_service_handler(self, service_spec: dict[str, Any]):
        """Return a deterministic runtime service handler."""
        def handler(payload: dict[str, Any]) -> dict[str, Any]:
            result = service_spec.get("result")
            if isinstance(result, dict):
                return dict(result)
            patch = service_spec.get("patch")
            if isinstance(patch, dict):
                return {"patch": dict(patch)}
            return {
                key: value
                for key, value in service_spec.items()
                if key not in {"name", "result"}
            }

        return handler

    def _inline_condition_handler(self, condition_spec: dict[str, Any]):
        """Return a deterministic plugin condition handler."""
        def handler(spec: dict[str, Any], context: Any) -> bool:
            if "result" in condition_spec:
                return bool(condition_spec["result"])
            return bool(spec.get("result", False))

        return handler

    def _register_plugin_object(self, registry: PluginRegistry, plugin: Any) -> None:
        """Register a plugin object that exposes register(api)."""
        if hasattr(plugin, "register"):
            plugin.register(PluginApi(registry))
            return
        raise TypeError("plugin factory 必须返回带 register(api) 方法的对象")
