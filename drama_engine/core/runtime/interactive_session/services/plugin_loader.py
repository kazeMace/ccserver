"""Load script-declared plugins for interactive_session."""

from __future__ import annotations

import importlib
from typing import Any

from drama_engine.core.dsl.plugins import PluginApi, PluginRegistry


class InteractivePluginLoader:
    """Load plugin declarations into a PluginRegistry."""

    def load(self, registry: PluginRegistry, plugin_specs: list[dict[str, Any]]) -> None:
        """Load all plugin specs declared by the script.

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
