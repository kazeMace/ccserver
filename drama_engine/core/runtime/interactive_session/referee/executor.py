"""Referee executor for interactive_session."""

from __future__ import annotations

from typing import Any

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
                    return str(result.get("end") or result.get("message") or "session_ended")
                return str(result or rule.get("message") or "session_ended")
        return None

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
