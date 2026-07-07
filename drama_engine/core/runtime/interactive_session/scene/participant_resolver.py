"""Scene 参与者解析器（从 SceneExecutor 拆出，M3）。

职责单一：把 scene.participants 的各种声明（static / list / all / filter / source /
from_state / service-evaluator）解析成一份合法的参与者名单，并应用 order_by / limit。
SceneExecutor 只负责编排，参与者「怎么选」收敛到这里。
"""

from __future__ import annotations

from typing import Any

from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.models import SceneSpec
from drama_engine.core.runtime.interactive_session.services.runtime_services import RuntimeServiceCaller


class ParticipantResolver:
    """把 scene.participants 声明解析为合法参与者名单。"""

    def __init__(self, services: RuntimeServiceCaller) -> None:
        """绑定运行时服务调用器（供 service-evaluator 型参与者选择使用）。"""
        assert services is not None, "services 不能为空"
        self._services = services

    async def resolve(self, ctx: InteractiveExecutionContext, scene: SceneSpec) -> list[str]:
        """解析 scene 参与者。"""
        spec = scene.participants.spec
        all_names = ctx.cast.all_names()
        if spec == "all":
            return list(all_names)
        if isinstance(spec, list):
            return [str(name) for name in spec if str(name) in all_names]
        if not isinstance(spec, dict):
            return []
        if "static" in spec:
            return [str(name) for name in spec.get("static", []) if str(name) in all_names]
        evaluator = spec.get("evaluator") or spec.get("provider")
        if not evaluator and spec.get("plugin"):
            evaluator = "plugin"
        if evaluator in {"plugin", "inside", "builtin", "http", "llm"}:
            return await self._resolve_service_participants(ctx, scene, spec, all_names)
        if "from_state" in spec or "from_state_set" in spec:
            value = ctx.value_resolver.resolve(
                {"ref": spec.get("from_state") or spec.get("from_state_set")},
                state=ctx.state,
                responses=ctx.last_responses,
                extra=ctx.runtime_extra(),
            )
            return self._coerce_participant_list(value, all_names, spec)
        if "filter" in spec or "source" in spec or "where" in spec:
            return await self._resolve_filter_participants(ctx, spec, all_names)
        return []

    async def _resolve_filter_participants(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        all_names: list[str],
    ) -> list[str]:
        """Resolve participants from source/filter/where selectors."""
        filter_spec = spec.get("filter") or {}
        if isinstance(filter_spec, dict) and ("source" in filter_spec or "where" in filter_spec):
            source = filter_spec.get("source")
            where = filter_spec.get("where") or {}
        else:
            source = spec.get("source")
            where = spec.get("where") or filter_spec
        candidates = self._participant_source(ctx, source, all_names)
        filtered = []
        for name in candidates:
            if name not in all_names:
                continue
            if not where:
                filtered.append(name)
            elif await ctx.condition_evaluator.evaluate_async(
                where,
                ctx.state,
                actor=name,
                entity=name,
                extra=ctx.condition_extra(),
            ):
                filtered.append(name)
        return self._apply_participant_options(filtered, ctx, spec)

    def _participant_source(
        self,
        ctx: InteractiveExecutionContext,
        source: Any,
        all_names: list[str],
    ) -> list[str]:
        """Resolve participant selector source names."""
        if source in (None, "agents", "actors", "cast"):
            return list(all_names)
        if source in {"GAME.players", "players"}:
            value = ctx.state.get_attr("GAME", "players") or []
            return [str(item) for item in value if str(item) in all_names]
        if source in {"participants", "scene_participants", "current_participants"}:
            current = ctx.session_metadata.get("interactive_current_participants") or []
            return [str(item) for item in current if str(item) in all_names]
        if isinstance(source, dict):
            value = ctx.value_resolver.resolve(
                source,
                state=ctx.state,
                responses=ctx.last_responses,
                extra=ctx.runtime_extra(),
            )
            return self._coerce_participant_list(value, all_names, {})
        if isinstance(source, str) and "." in source:
            value = ctx.value_resolver.resolve(
                {"ref": source},
                state=ctx.state,
                responses=ctx.last_responses,
                extra=ctx.runtime_extra(),
            )
            return self._coerce_participant_list(value, all_names, {})
        if isinstance(source, str):
            return [source] if source in all_names else []
        return []

    def _coerce_participant_list(
        self,
        value: Any,
        all_names: list[str],
        spec: dict[str, Any],
    ) -> list[str]:
        """Coerce a selector result into known cast names."""
        if value is None:
            result: list[str] = []
        elif isinstance(value, (list, tuple, set)):
            result = [str(item) for item in value if str(item) in all_names]
        else:
            text = str(value)
            result = [text] if text in all_names else []
        return self._apply_participant_options(result, None, spec)

    def _apply_participant_options(
        self,
        names: list[str],
        ctx: InteractiveExecutionContext | None,
        spec: dict[str, Any],
    ) -> list[str]:
        """Apply order_by and limit options to participant names."""
        result = list(dict.fromkeys(str(name) for name in names))
        order_by = spec.get("order_by")
        if ctx is not None and isinstance(order_by, str):
            result.sort(key=lambda name: (
                ctx.state.get_attr(name, order_by) is None,
                str(ctx.state.get_attr(name, order_by)),
                name,
            ))
        limit = spec.get("limit")
        if isinstance(limit, int):
            result = result[:limit]
        return result

    async def _resolve_service_participants(
        self,
        ctx: InteractiveExecutionContext,
        scene: SceneSpec,
        spec: dict[str, Any],
        all_names: list[str],
    ) -> list[str]:
        """Resolve participants through a runtime service or evaluator."""
        result = await self._services.call_async(
            ctx,
            spec,
            "participants",
            {
                **ctx.full_context_payload(),
                "scene": scene.id,
                "all_participants": list(all_names),
                "input": spec.get("input") or {},
            },
        )
        raw_items = (
            result.get("participants")
            or result.get("selected")
            or result.get("members")
            or result.get("result")
            or spec.get("fallback")
            or []
        )
        if isinstance(raw_items, str):
            raw_items = [raw_items]
        if not isinstance(raw_items, list):
            return []
        names = [str(name) for name in raw_items if str(name) in all_names]
        return self._apply_participant_options(names, ctx, spec)


__all__ = ["ParticipantResolver"]
