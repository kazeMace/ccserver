"""Referee executor for interactive_session."""

from __future__ import annotations

from typing import Any

from drama_engine.core.engine import SetAttr
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.models import RefereeSpec, SceneSpec


class RefereeExecutor:
    """Evaluate referee checks at configured lifecycle points."""

    def check(
        self,
        ctx: InteractiveExecutionContext,
        referee: RefereeSpec,
        hook: str,
        scene: SceneSpec | None = None,
        event: dict[str, Any] | None = None,
    ) -> str | None:
        """Run referee and return result text when ended."""
        if not referee.enabled:
            return None
        if hook not in set(referee.check_on):
            return None
        if not self._matches_include_exclude(referee, scene, event):
            return None

        if referee.evaluator:
            passed = ctx.condition_evaluator.evaluate(
                referee.evaluator,
                ctx.state,
                actor=None,
                responses=ctx.last_responses,
                extra={**ctx.runtime_extra(), "hook": hook, "event": event or {}},
            )
            if passed:
                if isinstance(referee.result, dict):
                    return self._apply_result(ctx, referee.result, scene, event)
                if referee.result is not None:
                    return str(referee.result)
                return "referee_passed"

        for rule in referee.rules:
            when = rule.get("when")
            if not isinstance(when, dict):
                continue
            try:
                passed = ctx.condition_evaluator.evaluate(
                    when,
                    ctx.state,
                    actor=None,
                    responses=ctx.last_responses,
                    extra={**ctx.runtime_extra(), "hook": hook, "event": event or {}},
                )
            except Exception as exc:  # noqa: BLE001 - visible warning is better than silent end.
                ctx.emit_host({
                    "kind": "interactive_session_warning",
                    "message": f"referee 条件求值失败: {exc}",
                    "scene": getattr(scene, "id", ""),
                    "hook": hook,
                })
                continue
            if passed:
                result = rule.get("result") or {}
                if isinstance(result, dict):
                    applied = self._apply_result(ctx, result, scene, event)
                    if applied is not None:
                        return applied
                    continue
                return str(result or rule.get("message") or "session_ended")
        return None

    def _apply_result(
        self,
        ctx: InteractiveExecutionContext,
        result: dict[str, Any],
        scene: SceneSpec | None,
        event: dict[str, Any] | None,
    ) -> str | None:
        """Apply structured referee result and return terminal text if ended."""
        effects = [self._normalize_effect(effect) for effect in result.get("effects", []) or []]
        if effects:
            ctx.effect_executor.execute_all(
                effects,
                ctx.state,
                ctx.writer,
                ctx.last_responses,
                actor=None,
                extra={**ctx.runtime_extra(), "scene_name": getattr(scene, "id", ""), "event": event or {}},
            )
        for item in result.get("set_state", []) or []:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            if not path or "." not in str(path):
                continue
            entity, attr = str(path).split(".", 1)
            if not ctx.state.has_entity(entity):
                ctx.state.register_entity(entity, {})
            value = ctx.value_resolver.resolve(
                item.get("value"),
                state=ctx.state,
                responses=ctx.last_responses,
                extra=ctx.runtime_extra(),
            )
            ctx.writer.apply(SetAttr(entity, attr, value))
        target = result.get("jump") or result.get("to")
        if target:
            ctx.session_metadata["interactive_next_target"] = str(target)
        if result.get("end") is not None:
            return str(result.get("end") or "session_ended")
        if result.get("message") is not None:
            return str(result["message"])
        if result.get("end_session") is True:
            return "session_ended"
        return None

    def _normalize_effect(self, effect: dict[str, Any]) -> dict[str, Any]:
        """Normalize set_state.path shorthand."""
        result = dict(effect)
        if result.get("type") == "set_state" and "path" in result:
            entity, attr = str(result.pop("path")).split(".", 1)
            result["entity"] = entity
            result["attr"] = attr
        return result

    def _matches_include_exclude(
        self,
        referee: RefereeSpec,
        scene: SceneSpec | None,
        event: dict[str, Any] | None,
    ) -> bool:
        """Check include/exclude filters."""
        if referee.include and not self._matches_filter(referee.include, scene, event):
            return False
        if referee.exclude and self._matches_filter(referee.exclude, scene, event):
            return False
        return True

    def _matches_filter(
        self,
        spec: Any,
        scene: SceneSpec | None,
        event: dict[str, Any] | None,
    ) -> bool:
        """Match scene/event filters."""
        scene_id = scene.id if scene is not None else None
        event_kind = (event or {}).get("kind")
        if isinstance(spec, list):
            return scene_id in spec
        if not isinstance(spec, dict):
            return False
        scenes = spec.get("scenes")
        if scenes is not None and scene_id not in scenes:
            return False
        event_kinds = spec.get("event_kinds")
        if event_kinds is not None and event_kind not in event_kinds:
            return False
        return True
