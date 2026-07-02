"""Dynamic-story execution model runner."""

from __future__ import annotations

import asyncio
from typing import Any

from drama_engine.core.execution_models.dynamic_story.domain_runtime import DynamicStoryDomainRuntime
from drama_engine.core.execution_models.dynamic_story.loop import StoryLoop
from drama_engine.core.execution_models.dynamic_story.policy import (
    DMPolicy,
    DynamicStoryPolicy,
    FreeActionInterpreter,
    LlmDmPolicy,
    NPCPolicy,
    StoryRuleChecker,
    StorySafetyBoundary,
    WorldConsistencyChecker,
)
from drama_engine.core.execution_models.dynamic_story.state import DynamicStoryState, WorldMemory
from drama_engine.core.session.state import SESSION_ASSIGNED, SESSION_ENDED, SESSION_RUNNING
from drama_engine.core.ports.memory import configure_runtime_memory_backend
from drama_engine.core.runner.base import BasicGameRunner
from drama_engine.core.runtime_spec.registry import RuntimeSpec

class DynamicStoryRunner(BasicGameRunner):
    """Runnable dynamic-story runtime."""

    def __init__(
        self,
        runtime: Any,
        declaration: RuntimeSpec,
        dry_run: bool = True,
        llm_client: Any = None,
    ) -> None:
        assert runtime is not None, "runtime 不能为空"
        assert declaration is not None, "declaration 不能为空"
        super().__init__(runtime=runtime, declaration=declaration, dry_run=dry_run)
        self._llm_client = llm_client
        self._state: DynamicStoryState | None = None
        self._world = WorldMemory()
        self._domain_runtime: DynamicStoryDomainRuntime | None = None
        self._loop: StoryLoop | None = None
        self._action_mode = "selected"

    async def reset_runtime_state(self) -> None:
        """Cancel current task and clear story memory."""
        runtime_state = self.runtime_state
        if runtime_state.task is not None and not runtime_state.task.done():
            runtime_state.task.cancel()
            try:
                await runtime_state.task
            except asyncio.CancelledError:
                pass
        runtime_state.task = None
        self._state = None
        self._domain_runtime = None
        self._loop = None

    async def assign(self) -> None:
        """Prepare story state and move session to assigned."""
        session_state = self.session_state
        assert session_state.status == "lobby", (
            f"只有 lobby 状态可以 assign，当前: {session_state.status}"
        )
        self._state = self._build_state()
        config = self._load_runtime_config()
        configure_runtime_memory_backend(self.context.memory_store, config)
        self._world = WorldMemory(self._state.world_state)
        self._domain_runtime = DynamicStoryDomainRuntime(
            state=self._state,
            world=self._world,
            policy=self._build_policy(config),
            memory_store=self.context.memory_store,
        )
        self._action_mode = self._story_action_mode(config)
        self.input_bridge.create_cast(
            actor_runtime=self.context.actor_runtime,
            player_names=list(self._state.players),
            human_seat_ids=set(getattr(session_state, "human_seat_ids", set())),
            action_service=self.runtime.action_service,
            dry_run=self.dry_run,
            step_gate=self.step_gate,
        )
        session_state.metadata["runtime_type"] = "dynamic_story"
        session_state.metadata["dynamic_story"] = {
            "world_name": self._state.world_name,
            "premise": self._state.premise,
            "players": list(self._state.players),
            "beat_count": len(self._state.beats),
        }
        session_state.set_status(SESSION_ASSIGNED)
        self.event_publisher.public({"kind": "session_assigned", "runtime_type": "dynamic_story"})
        self.event_publisher.host({"kind": "session_assigned", "runtime_type": "dynamic_story"})

    async def start(self) -> None:
        """Start the story beat loop."""
        session_state = self.session_state
        assert session_state.status == SESSION_ASSIGNED, (
            f"只有 assigned 状态可以 start，当前: {session_state.status}"
        )
        assert self._state is not None, "start 前必须先 assign"
        session_state.set_status(SESSION_RUNNING)
        self.event_publisher.public({"kind": "session_started", "runtime_type": "dynamic_story"})
        self.event_publisher.host({"kind": "session_started", "runtime_type": "dynamic_story"})
        self.runtime_state.task = asyncio.create_task(self._run_story())

    async def _run_story(self) -> None:
        """Run story beats, ask actors for actions, and end the session."""
        assert self._state is not None, "dynamic story state 不能为空"
        assert self._domain_runtime is not None, "dynamic story domain runtime 不能为空"
        cast = self.context.actor_runtime.cast
        assert cast is not None, "dynamic_story assign 后 Cast 不能为空"
        self._loop = StoryLoop(
            domain_runtime=self._domain_runtime,
            cast=cast,
            free_actions_for_beat=self._free_actions_for_beat,
            emit_public=self._emit_public,
            emit_views=self._emit_views,
            action_mode=self._action_mode,
        )
        try:
            await self._loop.run()
            self.session_state.metadata["dynamic_story"]["memory_size"] = len(self._state.memory)
            self.session_state.metadata["dynamic_story"]["world_memory"] = self._world.snapshot()
            self.session_state.metadata["dynamic_story"]["memory"] = list(self._state.memory)
            self.session_state.set_status(SESSION_ENDED)
            self._emit_public({
                "kind": "session_ended",
                "runtime_type": "dynamic_story",
                "result": "dynamic_story_completed",
            })
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - runtime errors must surface to host.
            self.session_state.set_status("failed")
            self.event_publisher.host({"kind": "session_failed", "error": str(exc)})

    def _build_state(self) -> DynamicStoryState:
        """Build story state from runtime config and session seats."""
        config = self._load_runtime_config()
        world = config.get("world") if isinstance(config.get("world"), dict) else {}
        world_name = str(config.get("world_name") or world.get("name") or "dynamic-world")
        premise = str(config.get("premise") or self._script_title() or "动态剧情")
        players = list(config.get("players") or self.session_state.seat_ids)
        assert players, "dynamic_story players 不能为空"
        beats = config.get("beats")
        if not isinstance(beats, list) or not beats:
            beats = [
                f"世界「{world_name}」建立，故事目标是：{premise}",
                "玩家描述行动，DM 根据世界状态给出后果。",
                "系统记录关键事件，作为后续剧情记忆。",
            ]
        return DynamicStoryState(
            world_name=world_name,
            premise=premise,
            players=[str(item) for item in players],
            beats=[str(item) for item in beats],
            world_state=dict(config.get("world_state") or {}),
        )

    def _load_runtime_config(self) -> dict[str, Any]:
        """Read runtime.config from the YAML script."""
        return self.config_parser.runtime_config(
            script_path=self.session_state.script_path,
            declaration=self.declaration,
        )

    def _script_title(self) -> str:
        """Return script title when available."""
        return self.config_parser.script_title(self.session_state.script_path)

    def _emit_public(self, event: dict[str, Any]) -> None:
        """Append one event to public and host streams."""
        self.event_publisher.public(dict(event))
        self.event_publisher.host(dict(event))

    def _free_actions_for_beat(self, beat_index: int) -> list[str]:
        """Read optional scripted free actions for a beat."""
        config = self._load_runtime_config()
        actions = config.get("free_actions")
        default_hint = str(config.get("default_action_hint") or "根据当前 beat 描述一个具体行动")
        if not isinstance(actions, list):
            return [default_hint]
        selected = []
        for item in actions:
            if isinstance(item, dict) and int(item.get("beat") or 0) == beat_index:
                selected.append(str(item.get("text") or "观察周围"))
            elif isinstance(item, str) and beat_index == 1:
                selected.append(item)
        return selected or [default_hint]

    def _build_policy(self, config: dict[str, Any]) -> DynamicStoryPolicy:
        """Build the configured default dynamic-story policy."""
        policy_spec = config.get("policy") if isinstance(config.get("policy"), dict) else {}
        interpreter_spec = policy_spec.get("interpreter") if isinstance(policy_spec.get("interpreter"), dict) else {}
        dm_spec = policy_spec.get("dm") if isinstance(policy_spec.get("dm"), dict) else {}
        intent_keywords = interpreter_spec.get("intent_keywords")
        normalized_keywords = None
        if isinstance(intent_keywords, dict):
            normalized_keywords = {
                str(intent): [str(item) for item in keywords]
                for intent, keywords in intent_keywords.items()
                if isinstance(keywords, list)
            }
        return DynamicStoryPolicy(
            action_interpreter=FreeActionInterpreter(intent_keywords=normalized_keywords),
            dm_policy=self._build_dm_policy(dm_spec, policy_spec),
            rule_checker=self._build_rule_checker(policy_spec),
            npc_policy=self._build_npc_policy(policy_spec),
            consistency_checker=self._build_consistency_checker(policy_spec),
            safety_boundary=self._build_safety_boundary(policy_spec),
            memory_store=self.context.memory_store,
            actor_strategy=str(policy_spec.get("actor_strategy") or config.get("actor_strategy") or "round_robin"),
            max_context_items=int(policy_spec.get("max_context_items", config.get("max_context_items", 3))),
        )

    def _build_dm_policy(self, dm_spec: dict[str, Any], policy_spec: dict[str, Any]) -> DMPolicy:
        """Build DM policy, optionally backed by an injected LLM client."""
        tone = str(dm_spec.get("tone") or policy_spec.get("dm_tone") or "neutral")
        provider = str(dm_spec.get("provider") or "template")
        if provider == "llm":
            assert self._llm_client is not None, "runtime.config.policy.dm.provider=llm 需要注入 llm_client"
            return LlmDmPolicy(
                llm_client=self._llm_client,
                tone=tone,
                system_prompt=str(dm_spec.get("system_prompt") or ""),
                fallback_policy=DMPolicy(tone=tone, consequence_template=str(dm_spec.get("fallback_template") or "")),
            )
        return DMPolicy(
            tone=tone,
            consequence_template=str(dm_spec.get("consequence_template") or ""),
        )

    @staticmethod
    def _build_rule_checker(policy_spec: dict[str, Any]) -> StoryRuleChecker:
        """Build configured story rule checker."""
        spec = policy_spec.get("rules") if isinstance(policy_spec.get("rules"), dict) else {}
        allowed = spec.get("allowed_intents")
        blocked = spec.get("blocked_keywords")
        return StoryRuleChecker(
            allowed_intents=[str(item) for item in allowed] if isinstance(allowed, list) else None,
            blocked_keywords=[str(item) for item in blocked] if isinstance(blocked, list) else None,
        )

    @staticmethod
    def _build_npc_policy(policy_spec: dict[str, Any]) -> NPCPolicy:
        """Build configured NPC policy."""
        spec = policy_spec.get("npc") if isinstance(policy_spec.get("npc"), dict) else {}
        npcs = spec.get("npcs")
        return NPCPolicy(npcs=[dict(item) for item in npcs] if isinstance(npcs, list) else None)

    @staticmethod
    def _build_consistency_checker(policy_spec: dict[str, Any]) -> WorldConsistencyChecker:
        """Build configured world consistency checker."""
        spec = policy_spec.get("world_consistency") if isinstance(policy_spec.get("world_consistency"), dict) else {}
        locations = spec.get("known_locations")
        immutable = spec.get("immutable_facts")
        return WorldConsistencyChecker(
            known_locations=[str(item) for item in locations] if isinstance(locations, list) else None,
            allow_unknown_location=bool(spec.get("allow_unknown_location", True)),
            immutable_facts=dict(immutable or {}) if isinstance(immutable, dict) else None,
        )

    @staticmethod
    def _build_safety_boundary(policy_spec: dict[str, Any]) -> StorySafetyBoundary:
        """Build configured story safety boundary."""
        spec = policy_spec.get("safety") if isinstance(policy_spec.get("safety"), dict) else {}
        forbidden = spec.get("forbidden_keywords")
        return StorySafetyBoundary(
            forbidden_keywords=[str(item) for item in forbidden] if isinstance(forbidden, list) else None,
            max_action_chars=int(spec.get("max_action_chars") or 600),
            replacement_text=str(spec.get("replacement_text") or "观察周围并等待安全时机"),
        )

    @staticmethod
    def _story_action_mode(config: dict[str, Any]) -> str:
        """Return selected/all_players action mode from runtime config."""
        policy_spec = config.get("policy") if isinstance(config.get("policy"), dict) else {}
        mode = str(policy_spec.get("action_mode") or config.get("action_mode") or "selected")
        assert mode in {"selected", "all_players"}, f"未知 dynamic_story action_mode: {mode}"
        return mode

    def _emit_views(self) -> None:
        """Emit ViewHost projections for dynamic story state."""
        assert self._domain_runtime is not None, "dynamic story domain runtime 不能为空"
        for event in self._domain_runtime.project_views():
            self._emit_public(event)

DynamicStoryExecutionModel = DynamicStoryRunner

__all__ = ["DynamicStoryExecutionModel", "DynamicStoryRunner"]
