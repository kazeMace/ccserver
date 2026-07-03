"""Dynamic-story execution loop."""

from __future__ import annotations

import asyncio
from typing import Any

from drama_engine.core.execution_models.dynamic_story.domain_runtime import DynamicStoryDomainRuntime

class StoryLoop:
    """Execute dynamic-story beats with actors and DM policy."""

    def __init__(
        self,
        domain_runtime: DynamicStoryDomainRuntime,
        cast: Any,
        free_actions_for_beat: Any,
        emit_public: Any,
        emit_views: Any,
        action_mode: str = "selected",
        check_referee: Any = None,
    ) -> None:
        assert domain_runtime is not None, "domain_runtime 不能为空"
        assert cast is not None, "cast 不能为空"
        assert free_actions_for_beat is not None, "free_actions_for_beat 不能为空"
        assert emit_public is not None, "emit_public 不能为空"
        assert emit_views is not None, "emit_views 不能为空"
        self.domain_runtime = domain_runtime
        self.cast = cast
        self.free_actions_for_beat = free_actions_for_beat
        self.emit_public = emit_public
        self.emit_views = emit_views
        self.action_mode = action_mode
        self.check_referee = check_referee

    async def run(self) -> str | None:
        """Run configured story beats and record memory events."""
        state = self.domain_runtime.state
        self.emit_public({
            "kind": "dynamic_story_opened",
            "world_name": state.world_name,
            "premise": state.premise,
            "players": list(state.players),
        })
        for index, beat in enumerate(state.beats, start=1):
            event = {
                "kind": "dynamic_story_beat",
                "index": index,
                "text": beat,
            }
            self.domain_runtime.remember(event, include_world=True)
            self.emit_public(event)
            for action_text in self.free_actions_for_beat(index):
                for actor_name in self.domain_runtime.policy.select_actors(
                    state,
                    index,
                    mode=self.action_mode,
                ):
                    acted_text = await self.actor_free_action(
                        beat_index=index,
                        action_hint=action_text,
                        actor_name=actor_name,
                    )
                    safety = self.domain_runtime.policy.review_raw_action(
                        actor_name,
                        acted_text,
                        len(state.memory) + 1,
                    )
                    if safety.event is not None:
                        self.domain_runtime.remember(safety.event)
                        self.emit_public(safety.event)
                    if not safety.allowed:
                        acted_text = safety.replacement_text
                    action = self.domain_runtime.policy.interpret_action(
                        actor_name,
                        acted_text,
                        len(state.memory) + 1,
                    )
                    rule_decision = self.domain_runtime.policy.check_action_rules(
                        action,
                        self.domain_runtime.world,
                    )
                    if rule_decision.event is not None:
                        self.domain_runtime.remember(rule_decision.event)
                        self.emit_public(rule_decision.event)
                    if not rule_decision.allowed:
                        continue
                    self.domain_runtime.remember(action)
                    self.emit_public(action)
                    for reaction in self.domain_runtime.policy.npc_reactions(
                        action,
                        self.domain_runtime.world,
                    ):
                        self.domain_runtime.remember(reaction)
                        self.emit_public(reaction)
                    before_ruling = self.domain_runtime.world.snapshot()
                    ruling = self.domain_runtime.policy.adjudicate(action, self.domain_runtime.world)
                    consistency = self.domain_runtime.policy.check_world_consistency(
                        ruling,
                        self.domain_runtime.world,
                    )
                    if consistency.event is not None:
                        self.domain_runtime.remember(consistency.event)
                        self.emit_public(consistency.event)
                    if not consistency.allowed:
                        self.domain_runtime.world.restore(before_ruling)
                        continue
                    self.domain_runtime.remember(ruling)
                    self.domain_runtime.remember_world()
                    self.emit_public(ruling)
                    verdict = self._check_referee("after_action", ruling)
                    if verdict is not None:
                        return verdict
            self.emit_views()
            verdict = self._check_referee("after_beat", event)
            if verdict is not None:
                return verdict
            await asyncio.sleep(0)
        return None

    def _check_referee(self, hook: str, event: dict[str, Any]) -> str | None:
        """Call optional runtime referee hook."""
        if self.check_referee is None:
            return None
        return self.check_referee(hook=hook, event=event)

    async def actor_free_action(self, beat_index: int, action_hint: str, actor_name: str) -> str:
        """Ask one selected actor to provide a free story action."""
        state = self.domain_runtime.state
        world = self.domain_runtime.world
        actor = self.cast.get(actor_name)
        await actor.perceive(self.domain_runtime.policy.perception_for(state, world))
        cue = self.domain_runtime.policy.cue_for(actor_name, beat_index, action_hint)
        response = await actor.act(cue)
        text = str((response or {}).get("text") or "").strip()
        return text or action_hint or "观察周围"

__all__ = ["StoryLoop"]
