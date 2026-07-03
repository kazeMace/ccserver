"""Scene executor for interactive_session."""

from __future__ import annotations

from typing import Any

from drama_engine.core.engine import SetAttr
from drama_engine.core.runtime.interactive_session.actions.controller import ControllerActionExecutor
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.models import SceneSpec
from drama_engine.core.runtime.interactive_session.referee.executor import RefereeExecutor
from drama_engine.core.runtime.interactive_session.schedule.executor import ScheduleExecutor


class SceneExecutor:
    """Execute one scene lifecycle."""

    def __init__(
        self,
        schedule_executor: ScheduleExecutor | None = None,
        controller_executor: ControllerActionExecutor | None = None,
        referee_executor: RefereeExecutor | None = None,
    ) -> None:
        """Initialize scene executor."""
        self._schedule = schedule_executor or ScheduleExecutor()
        self._controller = controller_executor or ControllerActionExecutor()
        self._referee = referee_executor or RefereeExecutor()

    async def execute(self, ctx: InteractiveExecutionContext, scene: SceneSpec) -> str | None:
        """Run a scene and return referee result when ended."""
        ctx.current_scene_id = scene.id
        if not self._when_passes(ctx, scene):
            ctx.emit_host({"kind": "interactive_scene_skipped", "scene": scene.id})
            return None

        self._run_hooks(ctx, scene, "on_enter")
        participants = self._resolve_participants(ctx, scene)
        cue = self._render_cue(ctx, scene.participant_action.cue)
        ctx.emit_public({
            "kind": "interactive_scene_started",
            "runtime_type": "interactive_session",
            "scene": scene.id,
            "participants": participants,
        })

        responses = await self._schedule.execute(
            ctx=ctx,
            schedule=scene.schedule,
            action=scene.participant_action,
            scope=scene.scope,
            participants=participants,
            cue=cue,
        )
        ctx.last_responses = responses
        result = self._check_referee_events(ctx, scene, "after_message", responses)
        if result is not None:
            return result
        result = self._check_referee_events(ctx, scene, "after_round", [{"kind": "round_completed"}])
        if result is not None:
            return result
        self._run_hooks(ctx, scene, "on_after_action")
        controller_result = await self._controller.execute(ctx, scene.controller_action)
        if isinstance(controller_result, dict) and controller_result.get("beat"):
            result = self._check_referee_events(ctx, scene, "after_generated_beat", [controller_result])
            if result is not None:
                return result
        self._apply_resolution(ctx, scene, responses, controller_result)
        self._publish(ctx, scene)
        result = self._referee.check(ctx, scene.referee, "after_scene", scene=scene)
        if result is None:
            result = self._referee.check(ctx, ctx.script.referee, "after_scene", scene=scene)
        self._run_hooks(ctx, scene, "on_exit")
        ctx.emit_public({
            "kind": "interactive_scene_completed",
            "runtime_type": "interactive_session",
            "scene": scene.id,
            "responses": len(responses),
            "result": result,
        })
        return result

    def _check_referee_events(
        self,
        ctx: InteractiveExecutionContext,
        scene: SceneSpec,
        hook: str,
        events: list[dict[str, Any]],
    ) -> str | None:
        """Run scene and top-level referee for event-like lifecycle hooks."""
        for event in events:
            result = self._referee.check(ctx, scene.referee, hook, scene=scene, event=event)
            if result is not None:
                return result
            result = self._referee.check(ctx, ctx.script.referee, hook, scene=scene, event=event)
            if result is not None:
                return result
        return None

    def _when_passes(self, ctx: InteractiveExecutionContext, scene: SceneSpec) -> bool:
        """Evaluate scene.when."""
        if not scene.when:
            return True
        return ctx.condition_evaluator.evaluate(
            scene.when,
            ctx.state,
            actor=None,
            responses=ctx.last_responses,
            extra=ctx.runtime_extra(),
        )

    def _resolve_participants(
        self,
        ctx: InteractiveExecutionContext,
        scene: SceneSpec,
    ) -> list[str]:
        """Resolve scene participants."""
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
        if "filter" in spec:
            filter_spec = spec.get("filter") or {}
            if isinstance(filter_spec, dict) and ("source" in filter_spec or "where" in filter_spec):
                source = filter_spec.get("source")
                where = filter_spec.get("where") or {}
            else:
                source = spec.get("source")
                where = spec.get("where") or filter_spec
            if source in {"GAME.players", "players"}:
                candidates = all_names
            else:
                candidates = sorted(ctx.condition_evaluator.filter_entities(filter_spec, ctx.state))
            filtered = []
            for name in candidates:
                if name not in all_names:
                    continue
                if not where:
                    filtered.append(name)
                elif ctx.condition_evaluator.evaluate(
                    where,
                    ctx.state,
                    actor=name,
                    entity=name,
                    extra=ctx.runtime_extra(),
                ):
                    filtered.append(name)
            return filtered
        return list(all_names)

    def _render_cue(self, ctx: InteractiveExecutionContext, cue: Any) -> str:
        """Render a cue spec or template."""
        if not cue:
            return ""
        if isinstance(cue, dict):
            text = str(cue.get("text") or "")
        else:
            text = str(cue)
        for entity in ctx.state.all_entities():
            marker_prefix = "{" + entity + "."
            if marker_prefix not in text:
                continue
            attrs = getattr(ctx.state, "_attrs", {}).get(entity, {})
            for attr in attrs:
                marker = "{" + entity + "." + attr + "}"
                value = ctx.state.get_attr(entity, attr)
                text = text.replace(marker, "" if value is None else str(value))
        return text

    def _apply_resolution(
        self,
        ctx: InteractiveExecutionContext,
        scene: SceneSpec,
        responses: list[dict[str, Any]],
        controller_result: dict[str, Any] | None,
    ) -> None:
        """Apply selection and effects."""
        resolution = scene.resolution or {}
        extra = {
            **ctx.runtime_extra(),
            "scene_name": scene.id,
            "controller_result": controller_result,
        }
        selection_result = self._selection(resolution.get("selection"), responses)
        if selection_result:
            extra["selection_result"] = selection_result
            extra["winner"] = selection_result.get("winner")
            self._ensure_entity(ctx, "RESOLUTION")
            ctx.writer.apply(SetAttr("RESOLUTION", "selected", selection_result.get("winner")))
        effects = [self._normalize_effect(effect) for effect in resolution.get("effects", []) or []]
        if effects:
            ctx.effect_executor.execute_all(
                effects=effects,
                state=ctx.state,
                writer=ctx.writer,
                responses=responses,
                actor=None,
                extra=extra,
            )

    def _selection(
        self,
        selection: Any,
        responses: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Compute a simple plurality selection."""
        if not isinstance(selection, dict):
            return None
        field = str(selection.get("field") or selection.get("target_field") or "vote")
        counts: dict[str, int] = {}
        for response in responses:
            data = response.get("data") or {}
            if not isinstance(data, dict):
                continue
            value = data.get(field)
            if value is None:
                continue
            counts[str(value)] = counts.get(str(value), 0) + 1
        if not counts:
            return {"winner": None, "counts": {}, "is_tie": False}
        highest = max(counts.values())
        winners = sorted(name for name, count in counts.items() if count == highest)
        tie_policy = str(selection.get("tie_policy") or "alphabetical")
        if len(winners) > 1 and tie_policy == "no_winner":
            winner = None
        elif len(winners) > 1 and tie_policy == "all_tied":
            winner = winners
        else:
            winner = winners[0]
        return {"winner": winner, "counts": counts, "is_tie": len(winners) > 1}

    def _normalize_effect(self, effect: dict[str, Any]) -> dict[str, Any]:
        """Normalize new effect path shorthand for existing EffectExecutor."""
        result = dict(effect)
        if result.get("type") == "set_state" and "path" in result:
            entity, attr = str(result.pop("path")).split(".", 1)
            result["entity"] = entity
            result["attr"] = attr
        return result

    def _publish(self, ctx: InteractiveExecutionContext, scene: SceneSpec) -> None:
        """Publish scene messages and disclosures."""
        publication = scene.publication or {}
        messages = publication.get("messages") or []
        for item in messages:
            if isinstance(item, str):
                text = item
                audience = scene.scope.id
            elif isinstance(item, dict):
                content = item.get("content") or {}
                text = content.get("text") or item.get("text") or ""
                audience_spec = item.get("audience") or {}
                audience = audience_spec.get("scope") if isinstance(audience_spec, dict) else audience_spec
            else:
                continue
            ctx.emit_public({
                "kind": "interactive_publication",
                "runtime_type": "interactive_session",
                "scene": scene.id,
                "audience": audience or scene.scope.id,
                "text": self._render_cue(ctx, text),
            })

    def _run_hooks(self, ctx: InteractiveExecutionContext, scene: SceneSpec, hook_name: str) -> None:
        """Run lifecycle hooks by reusing effects."""
        hook_items = scene.hooks.get(hook_name) or []
        if isinstance(hook_items, dict):
            hook_items = [hook_items]
        effects = []
        for item in hook_items:
            if not isinstance(item, dict):
                continue
            when = item.get("when")
            if when and not ctx.condition_evaluator.evaluate(when, ctx.state, actor=None, extra=ctx.runtime_extra()):
                continue
            if "do" in item:
                effects.extend(item.get("do") or [])
            elif item.get("type"):
                effects.append(item)
        normalized = [self._normalize_effect(effect) for effect in effects]
        if normalized:
            ctx.effect_executor.execute_all(
                normalized,
                ctx.state,
                ctx.writer,
                ctx.last_responses,
                actor=None,
                extra={**ctx.runtime_extra(), "scene_name": scene.id},
            )

    def _ensure_entity(self, ctx: InteractiveExecutionContext, entity: str) -> None:
        """Register an entity when missing."""
        if not ctx.state.has_entity(entity):
            ctx.state.register_entity(entity, {})
