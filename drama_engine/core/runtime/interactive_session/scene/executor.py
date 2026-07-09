"""Scene executor for interactive_session."""

from __future__ import annotations

from typing import Any

from drama_engine.core.components.value_resolver import parse_state_path
from drama_engine.core.engine import SetAttr
from drama_engine.core.runtime.interactive_session.actions.controller import ControllerActionExecutor
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.models import SceneSpec
from drama_engine.core.runtime.interactive_session.referee.executor import RefereeExecutor
from drama_engine.core.runtime.interactive_session.scene.participant_resolver import ParticipantResolver
from drama_engine.core.runtime.interactive_session.scene.publication_emitter import PublicationEmitter
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
        self._participants = ParticipantResolver(self._services)
        self._publication = PublicationEmitter(self._render_cue)

    async def execute(self, ctx: InteractiveExecutionContext, scene: SceneSpec) -> str | None:
        """Run a scene and return referee result when ended."""
        ctx.current_scene_id = scene.id
        ctx.notify_progress()
        if not await self._when_passes(ctx, scene):
            ctx.emit_host({"kind": "interactive_scene_skipped", "scene": scene.id})
            return None

        await self._run_hooks(ctx, scene, "on_enter")
        # on_enter 中的 emit_media 写入了 __pending_media，立即投递（不等 drain）
        had_video = self._drain_media_now(ctx, scene)
        # 标记是否有视频，供 cinematic controller 判断是否跳过对话逐条播放
        from drama_engine.core.engine import SetAttr as _SA
        ctx.writer.apply(_SA("GAME", "__last_scene_had_video", had_video))
        # 记录当前 flow node + 已访问节点（供剧情树面板）
        current_flow_node = ctx.current_state_id or ""
        if current_flow_node:
            ctx.writer.apply(_SA("GAME", "__current_flow_node", current_flow_node))
            visited = list(ctx.state.get_attr("GAME", "visited_nodes") or [])
            if current_flow_node not in visited:
                ctx.writer.apply(_SA("GAME", "visited_nodes", visited + [current_flow_node]))
        # 场景地点写入 State（供 view 端点返回，前端用于背景图切换，不进消息流）
        locations = scene.context.get("locations") if scene.context else None
        if locations:
            from drama_engine.core.engine import SetAttr as _SetAttr
            ctx.writer.apply(_SetAttr("SCENE", "locations", locations))
            ctx.writer.apply(_SetAttr("SCENE", "current_location", locations[0].get("name", "")))
        participants = await self._participants.resolve(ctx, scene)
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
        # publication 按 phase 分发：
        #   before_action（默认）= 在 controller 之前发布（玩家先看到内容再选择）
        #   after_action = 在 controller 之后发布（选择完成后展示结果）
        self._publication.drain_pending_broadcasts(ctx, scene)
        self._publish_by_phase(ctx, scene, "before_action")
        # 把 scene context 注入 metadata 供 cinematic controller 读取
        if scene.context:
            ctx.session_metadata["__cinematic_scene_context"] = scene.context
        controller_result = await self._controller.execute(ctx, scene.controller_action)
        if isinstance(controller_result, dict) and controller_result.get("beat"):
            result = await self._publish_generated_beats_until_referee(ctx, scene, controller_result)
            if result is not None:
                return await self._finish_scene(ctx, scene, responses, result, controller_result=controller_result)
        self._publish_by_phase(ctx, scene, "after_action")
        self._apply_resolution(ctx, scene, responses, controller_result)
        result = await self._check_referee_events(
            ctx,
            scene,
            "after_scene",
            [{"kind": "after_scene", "scene": scene.id, "responses": list(responses)}],
        )
        return await self._finish_scene(ctx, scene, responses, result, controller_result=controller_result)

    def _drain_media_now(self, ctx: InteractiveExecutionContext, scene: SceneSpec) -> bool:
        """立即投递 __pending_media 队列中的多媒体事件。返回是否有视频被投递。"""
        from drama_engine.core.engine import SetAttr
        media_pending = ctx.state.get_attr("GAME", "__pending_media") or []
        if not media_pending:
            return False
        ctx.writer.apply(SetAttr("GAME", "__pending_media", []))
        for item in media_pending:
            if not isinstance(item, dict):
                continue
            ctx.emit_public({
                "kind": item.get("kind") or "video",
                "runtime_type": "interactive_session",
                "scene": scene.id,
                "data": {
                    "url": item.get("url") or "",
                    "title": item.get("title") or "",
                    "poster": item.get("poster") or "",
                    "subtitle_url": item.get("subtitle_url") or "",
                    "autoplay": bool(item.get("autoplay", False)),
                },
            })
        return True

    def _publish_by_phase(self, ctx: InteractiveExecutionContext, scene: SceneSpec, current_phase: str) -> None:
        """按 phase 过滤发布 publication 内容。

        DSL 中 publication 的 messages/views 可指定 phase 字段：
          - "before_action"（默认）：在 controller 之前发布，玩家先看到内容
          - "after_action"：在 controller 之后发布，展示选择结果

        未指定 phase 的项默认为 "before_action"。
        """
        publication = scene.publication or {}
        # 按 phase 过滤 messages 和 views
        filtered_pub = {}
        for key in ("messages", "views", "disclosures"):
            items = publication.get(key) or []
            filtered = []
            for item in items:
                if isinstance(item, dict):
                    item_phase = item.get("phase", "before_action")
                else:
                    item_phase = "before_action"
                if item_phase == current_phase:
                    filtered.append(item)
            if filtered:
                filtered_pub[key] = filtered
        if not filtered_pub:
            return
        # 临时替换 scene.publication 来复用 emitter
        original = scene.publication
        scene.publication = filtered_pub
        self._publication.publish(ctx, scene)
        scene.publication = original

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
        controller_result: dict[str, Any] | None = None,
    ) -> str | None:
        """Run exit lifecycle and emit scene completion."""
        await self._run_hooks(
            ctx,
            scene,
            "on_exit",
            hook_extra={"controller_result": controller_result} if controller_result is not None else None,
        )
        self._publication.drain_pending_broadcasts(ctx, scene)
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
            entity, attr = parse_state_path(str(result.pop("path")))
            result["entity"] = entity
            result["attr"] = attr
        return result

    async def _run_hooks(
        self,
        ctx: InteractiveExecutionContext,
        scene: SceneSpec,
        hook_name: str,
        event: dict[str, Any] | None = None,
        hook_extra: dict[str, Any] | None = None,
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
                extra=ctx.condition_extra(event=event or {}, **(hook_extra or {})),
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
                extra={
                    **ctx.runtime_extra(),
                    "scene_name": scene.id,
                    "event": event or {},
                    **(hook_extra or {}),
                },
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
