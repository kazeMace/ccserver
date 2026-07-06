"""Runner for runtime.type=interactive_session."""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from typing import Any

from drama_engine.core.dsl.components import CandidateResolver, ConditionEvaluator, EffectExecutor, ValueResolver
from drama_engine.core.dsl.plugins import build_default_plugin_registry
from drama_engine.core.engine import SetAttr, State, StateWriter, Vocabulary
from drama_engine.core.ports.memory import configure_runtime_memory_backend
from drama_engine.core.runner.base import BasicGameRunner
from drama_engine.core.runtime.interactive_session.compiler import InteractiveSessionCompiler
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.flow.executor import FlowExecutor
from drama_engine.core.runtime.interactive_session.patch.journal import PatchJournal
from drama_engine.core.runtime.interactive_session.patch.materializer import FlowMaterializer
from drama_engine.core.runtime.interactive_session.services.plugin_loader import InteractivePluginLoader
from drama_engine.core.runtime_spec.registry import RuntimeSpec
from drama_engine.core.session.state import SESSION_ASSIGNED, SESSION_ENDED, SESSION_RUNNING

logger = logging.getLogger(__name__)


class InteractiveSessionRunner(BasicGameRunner):
    """Runnable interactive_session execution model."""

    def __init__(
        self,
        runtime: Any,
        declaration: RuntimeSpec,
        dry_run: bool = True,
    ) -> None:
        """Initialize runner."""
        assert runtime is not None, "runtime 不能为空"
        assert declaration is not None, "declaration 不能为空"
        super().__init__(runtime=runtime, declaration=declaration, dry_run=dry_run)
        self._compiler = InteractiveSessionCompiler()
        self._script = None
        self._ctx: InteractiveExecutionContext | None = None
        self._flow_executor = FlowExecutor()

    async def assign(self) -> None:
        """Compile script, create cast, and initialize runtime state."""
        session_state = self.session_state
        assert session_state.status == "lobby", (
            f"只有 lobby 状态可以 assign，当前: {session_state.status}"
        )
        config = self.config_parser.runtime_config(
            script_path=session_state.script_path,
            declaration=self.declaration,
        )
        configure_runtime_memory_backend(self.context.memory_store, config)
        script = self._compiler.compile(session_state.script_path, params=session_state.params)
        self._script = script
        player_names = self._resolve_player_names(script)
        self.input_bridge.create_cast(
            actor_runtime=self.context.actor_runtime,
            player_names=player_names,
            human_seat_ids=set(getattr(session_state, "human_seat_ids", set())),
            action_service=self.runtime.action_service,
            dry_run=self.dry_run,
            step_gate=self.step_gate,
        )
        state = self._build_state(script, player_names)
        plugins = build_default_plugin_registry()
        InteractivePluginLoader().load(plugins, script.plugins)
        evaluator = ConditionEvaluator(plugins)
        ctx = InteractiveExecutionContext(
            script=script,
            state=state,
            writer=StateWriter(state),
            cast=self.context.actor_runtime.cast,
            condition_evaluator=evaluator,
            effect_executor=EffectExecutor(evaluator, plugins),
            candidate_resolver=CandidateResolver(evaluator),
            value_resolver=ValueResolver(plugins),
            plugin_registry=plugins,
            patch_journal=PatchJournal(),
            emit_public=self._emit_public,
            emit_host=self._emit_host,
            session_metadata=session_state.metadata,
            emit_private=self._emit_private,
            base_raw=deepcopy(script.raw),
        )
        self._ctx = ctx
        session_state.metadata["human_seat_ids"] = list(getattr(session_state, "human_seat_ids", set()))
        session_state.metadata["runtime_type"] = "interactive_session"
        session_state.metadata["dry_run"] = self.dry_run
        session_state.metadata["project_root"] = str(getattr(self.context, "project_root", "") or "")
        session_state.metadata["interactive_session"] = {
            "flow_type": script.flow.type,
            "scene_count": len(script.scenes),
            "players": player_names,
            "base_flow": deepcopy(script.raw),
        }
        session_state.set_status(SESSION_ASSIGNED)
        self._emit_public({"kind": "session_assigned", "runtime_type": "interactive_session"})
        logger.info("[InteractiveSessionRunner] assign 完成 session=%s", session_state.session_id)

    async def start(self) -> None:
        """Start the interactive session flow."""
        session_state = self.session_state
        assert session_state.status == SESSION_ASSIGNED, (
            f"只有 assigned 状态可以 start，当前: {session_state.status}"
        )
        assert self._ctx is not None, "start 前必须先 assign"
        session_state.set_status(SESSION_RUNNING)
        self._emit_public({"kind": "session_started", "runtime_type": "interactive_session"})
        self.runtime_state.task = asyncio.create_task(self._run_flow())

    async def reset_runtime_state(self) -> None:
        """Cancel current task and clear transient runtime state."""
        runtime_state = self.runtime_state
        if runtime_state.task is not None and not runtime_state.task.done():
            runtime_state.task.cancel()
            try:
                await runtime_state.task
            except asyncio.CancelledError:
                pass
        runtime_state.task = None
        self._script = None
        self._ctx = None

    def summary(self, audience: str, seat_id: str | None = None) -> dict[str, Any]:
        """Return current summary for host/player views."""
        base = super().summary(audience, seat_id)
        if self._ctx is not None:
            base["interactive_session"] = {
                "current_state": self._ctx.current_state_id,
                "current_scene": self._ctx.current_scene_id,
                "patches": self._ctx.patch_journal.snapshot(),
                "base_flow": deepcopy(self._ctx.base_raw),
                "materialized_flow": FlowMaterializer().materialize(
                    self._ctx.script,
                    self._ctx.patch_journal,
                    self._ctx.base_raw,
                ),
            }
        return base

    async def _run_flow(self) -> None:
        """Run flow in background task."""
        assert self._ctx is not None, "interactive context 不能为空"
        try:
            result = await self._flow_executor.execute(self._ctx)
            self.session_state.metadata["interactive_session"]["result"] = result
            self.session_state.metadata["interactive_session"]["patches"] = self._ctx.patch_journal.snapshot()
            self.session_state.set_status(SESSION_ENDED)
            self._emit_public({
                "kind": "session_ended",
                "runtime_type": "interactive_session",
                "result": result,
            })
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - runner must surface failures.
            self.session_state.set_status("failed")
            self._emit_host({"kind": "session_failed", "runtime_type": "interactive_session", "error": str(exc)})
            logger.exception("[InteractiveSessionRunner] run_flow 失败")

    def _resolve_player_names(self, script: Any) -> list[str]:
        """Resolve player/agent seat names."""
        players = script.players or {}
        ids = players.get("ids") if isinstance(players, dict) else None
        if isinstance(ids, list) and ids:
            return [str(item) for item in ids]
        count = int(players.get("count") or 0) if isinstance(players, dict) else 0
        if count > 0:
            return [f"Player_{index}" for index in range(1, count + 1)]
        if self.session_state.seat_ids:
            return [str(item) for item in self.session_state.seat_ids]
        names = set()
        for scene in script.scenes.values():
            spec = scene.participants.spec
            if isinstance(spec, dict) and isinstance(spec.get("static"), list):
                names.update(str(item) for item in spec["static"])
            elif isinstance(spec, list):
                names.update(str(item) for item in spec)
        return sorted(names) or ["Player_1"]

    def _build_state(self, script: Any, player_names: list[str]) -> State:
        """Build open runtime State."""
        vocab = Vocabulary(
            roles=frozenset(),
            factions=frozenset(),
            scopes=frozenset(script.scopes.keys()),
            abilities=frozenset(),
        )
        state = State(vocab)
        state.register_entity("GAME", {"round": 1, "players": list(player_names), "ended": False})
        state.register_entity("STORY", {})
        state.register_entity("SCENE", {})
        for entity, attrs in (script.state or {}).items():
            if not state.has_entity(str(entity)):
                state.register_entity(str(entity), {})
            for key, value in (attrs or {}).items():
                StateWriter(state).apply(SetAttr(str(entity), str(key), value))
        player_initial = {}
        if isinstance(script.players, dict):
            player_initial = dict(script.players.get("initial_attrs") or {})
        player_initial.setdefault("alive", True)
        for name in player_names:
            if not state.has_entity(name):
                state.register_entity(name, dict(player_initial))
        return state

    def _emit_public(self, event: dict[str, Any]) -> None:
        """Publish event to public and host streams."""
        self.event_publisher.public(dict(event))
        self.event_publisher.host(dict(event))

    def _emit_host(self, event: dict[str, Any]) -> None:
        """Publish host-only event."""
        self.event_publisher.host(dict(event))

    def _emit_private(self, seat_id: str, event: dict[str, Any]) -> None:
        """Publish one private seat event."""
        self.event_publisher.private(seat_id, dict(event))


InteractiveSessionExecutionModel = InteractiveSessionRunner

__all__ = ["InteractiveSessionExecutionModel", "InteractiveSessionRunner"]
