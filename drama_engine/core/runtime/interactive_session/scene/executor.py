"""Scene executor for interactive_session."""

from __future__ import annotations

from typing import Any

from drama_engine.core.dsl.plugins import ViewContext
from drama_engine.core.engine import SetAttr
from drama_engine.core.runtime.interactive_session.actions.controller import ControllerActionExecutor
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.models import SceneSpec
from drama_engine.core.runtime.interactive_session.referee.executor import RefereeExecutor
from drama_engine.core.runtime.interactive_session.schedule.executor import ScheduleExecutor
from drama_engine.core.runtime.interactive_session.services.runtime_services import RuntimeServiceCaller


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
        self._services = RuntimeServiceCaller()

    async def execute(self, ctx: InteractiveExecutionContext, scene: SceneSpec) -> str | None:
        """Run a scene and return referee result when ended."""
        ctx.current_scene_id = scene.id
        if not await self._when_passes(ctx, scene):
            ctx.emit_host({"kind": "interactive_scene_skipped", "scene": scene.id})
            return None

        await self._run_hooks(ctx, scene, "on_enter")
        participants = await self._resolve_participants(ctx, scene)
        ctx.session_metadata["interactive_current_participants"] = list(participants)
        cue = self._render_cue(ctx, scene.participant_action.cue)
        ctx.emit_public({
            "kind": "interactive_scene_started",
            "runtime_type": "interactive_session",
            "scene": scene.id,
            "participants": participants,
        })

        await self._run_hooks(ctx, scene, "on_before_action")
        schedule_result = await self._schedule.execute(
            ctx=ctx,
            schedule=scene.schedule,
            action=scene.participant_action,
            scope=scene.scope,
            participants=participants,
            cue=cue,
            after_response=lambda response, current_responses: self._handle_message_event(
                ctx,
                scene,
                response,
                current_responses,
            ),
            after_round=lambda event, current_responses: self._handle_round_event(
                ctx,
                scene,
                event,
                current_responses,
            ),
            on_schedule_event=lambda event: self._handle_schedule_event(ctx, scene, event),
        )
        responses = list(schedule_result.get("responses") or [])
        ctx.last_responses = responses
        result = schedule_result.get("result")
        if result is not None:
            return await self._finish_scene(ctx, scene, responses, result)
        await self._run_hooks(ctx, scene, "on_after_action")
        controller_result = await self._controller.execute(ctx, scene.controller_action)
        if isinstance(controller_result, dict) and controller_result.get("beat"):
            result = await self._publish_generated_beats_until_referee(ctx, scene, controller_result)
            if result is not None:
                return await self._finish_scene(ctx, scene, responses, result)
        self._apply_resolution(ctx, scene, responses, controller_result)
        self._drain_pending_broadcasts(ctx, scene)
        self._publish(ctx, scene)
        result = await self._check_referee_events(
            ctx,
            scene,
            "after_scene",
            [{"kind": "after_scene", "scene": scene.id, "responses": list(responses)}],
        )
        return await self._finish_scene(ctx, scene, responses, result)

    async def _handle_message_event(
        self,
        ctx: InteractiveExecutionContext,
        scene: SceneSpec,
        response: dict[str, Any],
        current_responses: list[dict[str, Any]],
    ) -> str | None:
        """Run on_message hooks and after_message referee for one response."""
        ctx.last_responses = list(current_responses)
        await self._run_hooks(ctx, scene, "on_message", event=response)
        return await self._check_referee_events(ctx, scene, "after_message", [response])

    async def _handle_round_event(
        self,
        ctx: InteractiveExecutionContext,
        scene: SceneSpec,
        event: dict[str, Any],
        current_responses: list[dict[str, Any]],
    ) -> str | None:
        """Run after_round referee checks after one schedule round."""
        ctx.last_responses = list(current_responses)
        return await self._check_referee_events(ctx, scene, "after_round", [event])

    async def _finish_scene(
        self,
        ctx: InteractiveExecutionContext,
        scene: SceneSpec,
        responses: list[dict[str, Any]],
        result: str | None,
    ) -> str | None:
        """Run exit lifecycle and emit scene completion."""
        await self._run_hooks(ctx, scene, "on_exit")
        self._drain_pending_broadcasts(ctx, scene)
        ctx.emit_public({
            "kind": "interactive_scene_completed",
            "runtime_type": "interactive_session",
            "scene": scene.id,
            "responses": len(responses),
            "result": result,
        })
        return result

    async def _publish_generated_beats_until_referee(
        self,
        ctx: InteractiveExecutionContext,
        scene: SceneSpec,
        controller_result: dict[str, Any],
    ) -> str | None:
        """Emit generated beat events and stop as soon as referee ends."""
        beat = controller_result.get("beat") or {}
        beat_items = list(beat.get("beats") or [])
        if not beat_items:
            beat_items = [beat]
        for index, item in enumerate(beat_items):
            text = item.get("text") if isinstance(item, dict) else str(item)
            event = {
                "kind": "generated_beat",
                "runtime_type": "interactive_session",
                "scene": scene.id,
                "index": index,
                "text": text or "",
                "beat": item,
                "controller_result": controller_result,
            }
            ctx.record_message(event)
            ctx.emit_public(event)
            result = await self._check_referee_events(ctx, scene, "after_generated_beat", [event])
            if result is not None:
                return result
        next_result = await self._controller.continue_generated_beat(ctx, controller_result)
        if isinstance(next_result, dict) and next_result.get("beat"):
            return await self._publish_generated_beats_until_referee(ctx, scene, next_result)
        return None

    async def _check_referee_events(
        self,
        ctx: InteractiveExecutionContext,
        scene: SceneSpec,
        hook: str,
        events: list[dict[str, Any]],
    ) -> str | None:
        """Run scene and top-level referee for event-like lifecycle hooks."""
        for event in events:
            await self._run_hooks(ctx, scene, "on_referee_check", event=event)
            result = await self._referee.check(ctx, scene.referee, hook, scene=scene, event=event)
            if result is not None:
                return result
            result = await self._referee.check(ctx, ctx.script.referee, hook, scene=scene, event=event)
            if result is not None:
                return result
        return None

    async def _when_passes(self, ctx: InteractiveExecutionContext, scene: SceneSpec) -> bool:
        """Evaluate scene.when."""
        if not scene.when:
            return True
        return await ctx.condition_evaluator.evaluate_async(
            scene.when,
            ctx.state,
            actor=None,
            responses=ctx.last_responses,
            extra=ctx.condition_extra(),
        )

    async def _resolve_participants(
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
        selection_result = self._selection(
            ctx,
            resolution.get("selection"),
            responses,
            controller_result,
        )
        if selection_result:
            extra["selection_result"] = selection_result
            extra["winner"] = selection_result.get("winner")
            self._ensure_entity(ctx, "RESOLUTION")
            ctx.writer.apply(SetAttr("RESOLUTION", "selected", selection_result.get("winner")))
            ctx.writer.apply(SetAttr("RESOLUTION", "counts", selection_result.get("counts", {})))
            ctx.writer.apply(SetAttr("RESOLUTION", "needs_runoff", bool(selection_result.get("needs_runoff"))))
            ctx.writer.apply(SetAttr(
                "RESOLUTION",
                "runoff_candidates",
                selection_result.get("runoff_candidates", []),
            ))
            self._apply_runoff_target(ctx, resolution.get("selection"), selection_result)
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

    def _apply_runoff_target(
        self,
        ctx: InteractiveExecutionContext,
        selection: Any,
        selection_result: dict[str, Any],
    ) -> None:
        """Store a configured runoff target when a tie needs another round."""
        if not selection_result.get("needs_runoff") or not isinstance(selection, dict):
            return
        runoff = selection.get("runoff")
        target = None
        if isinstance(runoff, dict):
            target = runoff.get("to") or runoff.get("scene") or runoff.get("state")
        target = target or selection.get("runoff_to") or selection.get("runoff_scene")
        if target:
            ctx.session_metadata["interactive_next_target"] = str(target)
            ctx.emit_host({
                "kind": "interactive_runoff_requested",
                "scene": ctx.current_scene_id,
                "target": str(target),
                "candidates": list(selection_result.get("runoff_candidates") or []),
            })

    def _selection(
        self,
        ctx: InteractiveExecutionContext,
        selection: Any,
        responses: list[dict[str, Any]],
        controller_result: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Compute a simple plurality selection."""
        if not isinstance(selection, dict):
            return None
        field = str(selection.get("field") or selection.get("target_field") or "vote")
        counts: dict[str, int] = {}
        for item in self._selection_items(ctx, selection, responses, controller_result):
            value = self._selection_value(item, field)
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
        elif len(winners) > 1 and tie_policy == "runoff":
            return {
                "winner": None,
                "counts": counts,
                "is_tie": True,
                "tie_policy": tie_policy,
                "runoff_candidates": winners,
                "needs_runoff": True,
            }
        else:
            winner = winners[0]
        return {
            "winner": winner,
            "counts": counts,
            "is_tie": len(winners) > 1,
            "tie_policy": tie_policy,
        }

    def _selection_items(
        self,
        ctx: InteractiveExecutionContext,
        selection: dict[str, Any],
        responses: list[dict[str, Any]],
        controller_result: dict[str, Any] | None,
    ) -> list[Any]:
        """Resolve selection.source into countable items."""
        source = selection.get("source", "responses")
        if source in (None, "responses"):
            return list(responses)
        if source == "controller_result":
            return [controller_result] if controller_result else []
        value = ctx.value_resolver.resolve(
            source,
            state=ctx.state,
            responses=responses,
            extra={
                **ctx.runtime_extra(),
                "controller_result": controller_result,
            },
        )
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return list(value.values()) if selection.get("values") else [value]
        return [value]

    def _selection_value(self, item: Any, field: str) -> Any:
        """Read one selection value from a response/data item."""
        if isinstance(item, dict):
            data = item.get("data")
            if isinstance(data, dict) and field in data:
                return data.get(field)
            if field in item:
                return item.get(field)
        return item

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
                text = self._render_cue(ctx, item)
                audience = scene.scope.id
            elif isinstance(item, dict):
                text = self._publication_text(ctx, item)
                audience = item.get("audience") or item.get("scope") or scene.scope.id
            else:
                continue
            event = {
                "kind": "interactive_publication",
                "runtime_type": "interactive_session",
                "scene": scene.id,
                "audience": self._audience_label(audience, scene.scope.id),
                "text": text,
            }
            self._emit_to_audience(ctx, event, audience, default_scope=scene.scope.id, private_default=False)
        for item in publication.get("disclosures", []) or []:
            if not isinstance(item, dict):
                continue
            audience = item.get("audience") or item.get("scope") or scene.scope.id
            text = self._publication_text(ctx, item)
            event = {
                "kind": "interactive_disclosure",
                "runtime_type": "interactive_session",
                "scene": scene.id,
                "audience": self._audience_label(audience, scene.scope.id),
                "text": text,
            }
            self._emit_to_audience(
                ctx,
                event,
                audience,
                default_scope=scene.scope.id,
                private_default=bool(item.get("private", True)),
            )
        for view in publication.get("views", []) or []:
            if not isinstance(view, dict):
                continue
            try:
                audience_spec = view.get("audience") or view.get("scope") or scene.scope.id
                audience_label = self._audience_label(audience_spec, scene.scope.id)
                projector_spec = {**view, "audience": audience_label}
                view_event = ctx.plugin_registry.project_view(
                    projector_spec,
                    ViewContext(
                        state=ctx.state,
                        scene_name=scene.id,
                        audience=str(audience_label),
                        mutation_log=ctx.state.mutation_log(),
                        script_extensions={},
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - publication failure should be visible.
                ctx.emit_host({
                    "kind": "interactive_session_warning",
                    "message": f"publication.views 投影失败: {exc}",
                    "scene": scene.id,
                })
                continue
            if view_event:
                self._emit_to_audience(
                    ctx,
                    view_event,
                    audience_spec,
                    default_scope=scene.scope.id,
                    private_default=bool(view.get("private") or view_event.get("private")),
                )

    def _publication_text(
        self,
        ctx: InteractiveExecutionContext,
        item: dict[str, Any],
    ) -> str:
        """Resolve publication text/template/ref content."""
        content = item.get("content") or item.get("message") or {}
        if isinstance(content, str):
            return self._render_cue(ctx, content)
        if not isinstance(content, dict):
            content = {}
        if "ref" in content:
            value = ctx.value_resolver.resolve(
                {"ref": content["ref"]},
                state=ctx.state,
                responses=ctx.last_responses,
                extra=ctx.runtime_extra(),
            )
            return "" if value is None else str(value)
        text = (
            content.get("text")
            or content.get("template")
            or item.get("text")
            or item.get("template")
            or ""
        )
        return self._render_cue(ctx, text)

    def _emit_to_audience(
        self,
        ctx: InteractiveExecutionContext,
        event: dict[str, Any],
        audience: Any,
        default_scope: str,
        private_default: bool,
    ) -> None:
        """Route a publication event to public, host, or private players."""
        if isinstance(audience, dict):
            players = audience.get("players") or audience.get("seats")
            if isinstance(players, list) and players:
                for seat_id in players:
                    self._emit_private(ctx, str(seat_id), event)
                return
            scope_name = audience.get("scope") or audience.get("id") or default_scope
            visibility = audience.get("visibility")
            if visibility == "private" or private_default:
                members = audience.get("members") or []
                for seat_id in members:
                    self._emit_private(ctx, str(seat_id), event)
                if not members:
                    ctx.emit_host(event)
                return
            ctx.emit_public({**event, "audience": scope_name})
            return
        if private_default:
            ctx.emit_host(event)
            return
        ctx.emit_public({**event, "audience": audience or default_scope})

    def _emit_private(
        self,
        ctx: InteractiveExecutionContext,
        seat_id: str,
        event: dict[str, Any],
    ) -> None:
        """Emit a private event when the runtime provides a private sink."""
        if ctx.emit_private is not None:
            ctx.emit_private(seat_id, event)
            return
        ctx.emit_host({**event, "seat_id": seat_id})

    def _audience_label(self, audience: Any, default_scope: str) -> Any:
        """Return a compact audience label for event payloads."""
        if isinstance(audience, dict):
            return audience.get("scope") or audience.get("id") or audience.get("players") or default_scope
        return audience or default_scope

    def _drain_pending_broadcasts(
        self,
        ctx: InteractiveExecutionContext,
        scene: SceneSpec,
    ) -> None:
        """Publish and clear effects.broadcast messages for interactive_session."""
        pending = ctx.state.get_attr("GAME", "__pending_broadcasts") or []
        if not pending:
            return
        ctx.writer.apply(SetAttr("GAME", "__pending_broadcasts", []))
        for item in pending:
            if not isinstance(item, dict):
                continue
            audience = item.get("scope") or scene.scope.id
            text = str(item.get("template") or item.get("text") or "")
            if not text:
                continue
            event = {
                "kind": "interactive_broadcast",
                "runtime_type": "interactive_session",
                "scene": scene.id,
                "audience": self._audience_label(audience, scene.scope.id),
                "text": self._render_cue(ctx, text),
            }
            self._emit_to_audience(ctx, event, audience, scene.scope.id, private_default=False)

    async def _run_hooks(
        self,
        ctx: InteractiveExecutionContext,
        scene: SceneSpec,
        hook_name: str,
        event: dict[str, Any] | None = None,
    ) -> None:
        """Run lifecycle hooks by reusing effects."""
        hook_items = scene.hooks.get(hook_name) or []
        if isinstance(hook_items, dict):
            hook_items = [hook_items]
        effects = []
        for item in hook_items:
            if not isinstance(item, dict):
                continue
            when = item.get("when")
            if when and not await ctx.condition_evaluator.evaluate_async(
                when,
                ctx.state,
                actor=None,
                responses=ctx.last_responses,
                extra=ctx.condition_extra(event=event or {}),
            ):
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
                extra={**ctx.runtime_extra(), "scene_name": scene.id, "event": event or {}},
            )

    async def _handle_schedule_event(
        self,
        ctx: InteractiveExecutionContext,
        scene: SceneSpec,
        event: dict[str, Any],
    ) -> None:
        """Run schedule push/pop hooks at the lifecycle point that created them."""
        if event.get("kind") == "push_schedule":
            await self._run_hooks(ctx, scene, "on_schedule_push", event=event)
        elif event.get("kind") == "pop_schedule":
            await self._run_hooks(ctx, scene, "on_schedule_pop", event=event)

    def _ensure_entity(self, ctx: InteractiveExecutionContext, entity: str) -> None:
        """Register an entity when missing."""
        if not ctx.state.has_entity(entity):
            ctx.state.register_entity(entity, {})
