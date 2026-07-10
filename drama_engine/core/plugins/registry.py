"""Script DSL 插件注册表与通用视图事件。

本模块只定义小而稳定的扩展接口。插件可以扩展规则、条件、值解析和视图投影，
但不能直接接管 Director 主循环。会修改 State 的能力必须通过 StateWriter 执行。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import inspect
import threading
from typing import Any, Callable

from drama_engine.core.components.value_resolver import ValueResolver


@dataclass
class EffectContext:
    """运行 effect handler 时可见的最小上下文。"""

    state: Any
    writer: Any
    actor: str | None
    responses: list
    scene_name: str
    extra: dict


@dataclass
class ViewContext:
    """运行 view projector 时可见的最小上下文。"""

    state: Any
    scene_name: str
    audience: str
    mutation_log: list


@dataclass
class ViewEvent:
    """后端发给前端 ViewHost 的结构化展示事件。"""

    view_id: str
    view_kind: str
    title: str
    audience: str
    data: dict
    private: bool = False
    priority: int = 0
    layout: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """转为可 JSON 序列化的事件字典。"""
        assert self.view_id, "ViewEvent.view_id 不能为空"
        assert self.view_kind, "ViewEvent.view_kind 不能为空"
        assert self.audience, "ViewEvent.audience 不能为空"
        return {
            "kind": "__view__",
            "view_id": self.view_id,
            "view_kind": self.view_kind,
            "title": self.title or self.view_id,
            "audience": self.audience,
            "private": self.private,
            "priority": self.priority,
            "layout": self.layout or {},
            "data": self.data or {},
            "meta": self.meta or {},
        }


class PluginRegistry:
    """插件能力注册表。核心引擎依赖该抽象，而不是依赖具体插件。"""

    def __init__(self) -> None:
        self._effects: dict[str, Callable[[dict, EffectContext], None]] = {}
        self._conditions: dict[str, Callable[[dict, Any], bool]] = {}
        self._value_resolvers: dict[str, Callable[[str, Any], Any]] = {}
        self._view_projectors: dict[str, Callable[[dict, ViewContext], ViewEvent | dict | None]] = {}
        self._runtime_services: dict[str, Callable[[dict], dict | None]] = {}
        self._validators: list[Callable[[dict], list[str]]] = []

    def register_effect(self, name: str, handler: Callable[[dict, EffectContext], None]) -> None:
        """注册一个 effect handler。"""
        assert name and isinstance(name, str), "effect 名称必须是非空字符串"
        assert callable(handler), f"effect handler 不可调用: {name}"
        self._effects[name] = handler

    def has_effect(self, name: str) -> bool:
        """检查是否存在指定 effect。"""
        return name in self._effects

    def execute_effect(self, effect: dict, context: EffectContext) -> bool:
        """执行插件 effect。返回 True 表示已处理。"""
        effect_type = effect.get("type")
        handler = self._effects.get(effect_type)
        if handler is None:
            return False
        handler(effect, context)
        return True

    def register_condition(self, name: str, handler: Callable[[dict, Any], bool]) -> None:
        """注册一个 condition handler。"""
        assert name and isinstance(name, str), "condition 名称必须是非空字符串"
        assert callable(handler), f"condition handler 不可调用: {name}"
        self._conditions[name] = handler

    def evaluate_condition(self, name: str, spec: dict, context: Any) -> bool:
        """执行插件 condition。"""
        handler = self._conditions.get(name)
        if handler is None:
            raise ValueError(f"未知 plugin condition: {name}")
        result = handler(spec, context)
        if inspect.isawaitable(result):
            result = self._await_sync(result)
        return bool(result)

    async def evaluate_condition_async(self, name: str, spec: dict, context: Any) -> bool:
        """执行插件 condition，支持 async handler。"""
        handler = self._conditions.get(name)
        if handler is None:
            raise ValueError(f"未知 plugin condition: {name}")
        result = handler(spec, context)
        if inspect.isawaitable(result):
            result = await result
        return bool(result)

    def _await_sync(self, value: Any) -> Any:
        """Wait for an awaitable from a sync compatibility path."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(value)
        result: dict[str, Any] = {}

        def run_in_thread() -> None:
            try:
                result["value"] = asyncio.run(value)
            except BaseException as exc:  # noqa: BLE001 - re-raise in caller thread.
                result["error"] = exc

        thread = threading.Thread(target=run_in_thread, daemon=True)
        thread.start()
        thread.join()
        if "error" in result:
            raise result["error"]
        return result.get("value")

    def has_condition(self, name: str) -> bool:
        """检查是否存在指定 condition。"""
        return name in self._conditions

    def register_value_resolver(self, prefix: str, resolver: Callable[[str, Any], Any]) -> None:
        """注册一个值解析前缀。"""
        assert prefix and isinstance(prefix, str), "resolver prefix 必须是非空字符串"
        assert callable(resolver), f"value resolver 不可调用: {prefix}"
        self._value_resolvers[prefix] = resolver

    def resolve_value(self, ref: str, context: Any) -> Any:
        """按 prefix 执行插件值解析器。"""
        prefix, sep, rest = ref.partition(":")
        if not sep:
            raise ValueError(f"插件值引用必须是 prefix:path 格式: {ref}")
        resolver = self._value_resolvers.get(prefix)
        if resolver is None:
            raise ValueError(f"未知 plugin value resolver: {prefix}")
        return resolver(rest, context)

    def has_value_resolver(self, prefix: str) -> bool:
        """检查是否存在指定值解析前缀。"""
        return prefix in self._value_resolvers

    def register_runtime_service(self, name: str, handler: Callable[[dict], dict | None]) -> None:
        """注册一个 runtime service handler。"""
        assert name and isinstance(name, str), "runtime service 名称必须是非空字符串"
        assert callable(handler), f"runtime service handler 不可调用: {name}"
        self._runtime_services[name] = handler

    def has_runtime_service(self, name: str) -> bool:
        """检查是否存在指定 runtime service。"""
        return name in self._runtime_services

    def call_runtime_service(self, name: str, payload: dict) -> dict | None:
        """执行 runtime service handler。"""
        handler = self._runtime_services.get(name)
        if handler is None:
            raise ValueError(f"未知 runtime service: {name}")
        return handler(payload)

    def register_view_projector(
        self,
        name: str,
        projector: Callable[[dict, ViewContext], ViewEvent | dict | None],
    ) -> None:
        """注册一个 ViewProjector。"""
        assert name and isinstance(name, str), "view projector 名称必须是非空字符串"
        assert callable(projector), f"view projector 不可调用: {name}"
        self._view_projectors[name] = projector

    def project_view(self, spec: dict, context: ViewContext) -> dict | None:
        """把 publication.views 条目投影为 ViewEvent 字典。"""
        assert isinstance(spec, dict), "view spec 必须是字典"
        projector_name = spec.get("projector") or "core.views.inline"
        projector = self._view_projectors.get(projector_name)
        if projector is None:
            raise ValueError(f"未知 view projector: {projector_name}")
        event = projector(spec, context)
        if event is None:
            return None
        if isinstance(event, ViewEvent):
            return event.to_dict()
        assert isinstance(event, dict), f"ViewProjector 必须返回 ViewEvent 或 dict，收到 {type(event)}"
        event.setdefault("kind", "__view__")
        return event

    def register_validator(self, validator: Callable[[dict], list[str]]) -> None:
        """注册一个剧本校验器。"""
        assert callable(validator), "validator 必须可调用"
        self._validators.append(validator)

    def validate(self, doc: dict) -> list[str]:
        """运行所有插件校验器。"""
        errors: list[str] = []
        for validator in self._validators:
            result = validator(doc)
            if result:
                errors.extend(result)
        return errors


class PluginApi:
    """暴露给插件的窄接口。"""

    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    def register_effect(self, name: str, handler: Callable[[dict, EffectContext], None]) -> None:
        """注册 effect handler。"""
        self._registry.register_effect(name, handler)

    def register_condition(self, name: str, handler: Callable[[dict, Any], bool]) -> None:
        """注册 condition handler。"""
        self._registry.register_condition(name, handler)

    def register_value_resolver(self, prefix: str, resolver: Callable[[str, Any], Any]) -> None:
        """注册 value resolver。"""
        self._registry.register_value_resolver(prefix, resolver)

    def register_runtime_service(self, name: str, handler: Callable[[dict], dict | None]) -> None:
        """注册 runtime service。"""
        self._registry.register_runtime_service(name, handler)

    def register_view_projector(
        self,
        name: str,
        projector: Callable[[dict, ViewContext], ViewEvent | dict | None],
    ) -> None:
        """注册 view projector。"""
        self._registry.register_view_projector(name, projector)

    def register_validator(self, validator: Callable[[dict], list[str]]) -> None:
        """注册剧本校验器。"""
        self._registry.register_validator(validator)


class CoreViewsPlugin:
    """内置通用看板插件。只投影视图，不修改游戏状态。"""

    def register(self, api: PluginApi) -> None:
        """注册内置 projector。"""
        api.register_view_projector("core.views.inline", self._project_inline)
        api.register_view_projector("core.views.state_attr", self._project_state_attr)
        api.register_view_projector("core.views.media", self._project_media)

    def _project_inline(self, spec: dict, context: ViewContext) -> ViewEvent:
        """把 view spec 中的 data 直接解析为 ViewEvent。"""
        data_spec = spec.get("data")
        if data_spec is None:
            data_spec = self._collect_convenience_data(spec)
        data = self._resolve_view_data(data_spec or {}, context)
        return self._build_event(spec, context, data if isinstance(data, dict) else {"value": data})

    def _project_state_attr(self, spec: dict, context: ViewContext) -> ViewEvent:
        """把某个 State 路径包装为 key-value 视图。"""
        resolver = ValueResolver()
        value = resolver.resolve(spec.get("source"), state=context.state, extra={"__state": context.state})
        label = spec.get("label") or spec.get("title") or "状态"
        data = {"rows": [{"label": label, "value": value if value is not None else ""}]}
        return self._build_event(spec, context, data)

    def _collect_convenience_data(self, spec: dict) -> dict:
        """兼容更短的看板写法，如直接写 rows/items/groups。"""
        data = {}
        for key in ("rows", "items", "groups", "columns", "cells", "text", "progress"):
            if key in spec:
                data[key] = spec[key]
        return data

    def _resolve_view_data(self, value: Any, context: ViewContext) -> Any:
        """
        解析视图数据中的引用。

        View data 里常有业务字段 `value`，不能把任意包含 value 的 dict 都当成
        ValueResolver 表达式；只有 `{ref: ...}` 和 `{state: ...}` 是引用。
        """
        resolver = ValueResolver()
        if isinstance(value, dict):
            if set(value.keys()) == {"ref"} or set(value.keys()) == {"state"}:
                return resolver.resolve(value, state=context.state, extra={"__state": context.state})
            return {
                key: self._resolve_view_data(item, context)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._resolve_view_data(item, context) for item in value]
        return value

    def _project_media(self, spec: dict, context: ViewContext) -> ViewEvent:
        """投影媒体资产（image/video/audio）并校验必填字段。

        校验规则：
          - url 必填（或通过 {ref:} 动态解析）
          - 可选字段：mime, poster, subtitle_url, alt, caption, autoplay, loop, duration_ms
        """
        data_spec = spec.get("data") or {}
        data = self._resolve_view_data(data_spec, context)
        if not isinstance(data, dict):
            data = {"url": str(data) if data else ""}
        # 校验 url 必填
        url = data.get("url")
        if not url:
            raise ValueError(f"媒体 view (id={spec.get('id')}) 缺少必填字段 'url'")
        # 标准化可选字段（保留原样，不做额外处理）
        normalized = {
            "url": str(url),
            "mime": str(data.get("mime") or ""),
            "poster": str(data.get("poster") or ""),
            "subtitle_url": str(data.get("subtitle_url") or ""),
            "alt": str(data.get("alt") or data.get("caption") or ""),
            "autoplay": bool(data.get("autoplay", False)),
            "loop": bool(data.get("loop", False)),
        }
        if "duration_ms" in data:
            normalized["duration_ms"] = int(data["duration_ms"])
        # 保留其他自定义字段
        for key, value in data.items():
            if key not in normalized:
                normalized[key] = value
        return self._build_event(spec, context, normalized)

    def _build_event(self, spec: dict, context: ViewContext, data: dict) -> ViewEvent:
        """根据通用字段构造 ViewEvent。"""
        view_id = spec.get("id") or spec.get("view_id")
        view_kind = spec.get("kind") or spec.get("view_kind") or "key-value"
        audience = spec.get("audience") or context.audience
        assert view_id, f"publication.views 条目缺少 id: {spec}"
        return ViewEvent(
            view_id=view_id,
            view_kind=view_kind,
            title=spec.get("title") or view_id,
            audience=audience,
            private=bool(spec.get("private", False)),
            priority=int(spec.get("priority", 0) or 0),
            layout=dict(spec.get("layout") or {}),
            data=data,
            meta={
                "source_plugin": spec.get("projector", "core.views.inline"),
                "scene": context.scene_name,
            },
        )



def build_default_plugin_registry() -> PluginRegistry:
    """构建默认插件注册表，包含 core.views。"""
    registry = PluginRegistry()
    api = PluginApi(registry)
    CoreViewsPlugin().register(api)
    return registry
