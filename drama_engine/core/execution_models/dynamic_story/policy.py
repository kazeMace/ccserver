"""Dynamic-story policies, safety checks, and DM adjudication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from drama_engine.core.execution_models.dynamic_story.state import DynamicStoryState, WorldMemory

class FreeActionInterpreter:
    """Interpret free player actions into story events."""

    def __init__(self, intent_keywords: dict[str, list[str]] | None = None) -> None:
        self.intent_keywords = intent_keywords or {
            "conflict": ["攻击", "fight", "attack"],
            "social": ["交谈", "talk", "ask"],
            "investigate": ["调查", "inspect", "search"],
        }

    def interpret(self, actor: str, text: str, index: int) -> dict[str, Any]:
        """Turn free text into a structured action event."""
        action = str(text or "").strip() or "观察周围"
        intent = "explore"
        for candidate_intent, keywords in self.intent_keywords.items():
            if any(word in action for word in keywords):
                intent = candidate_intent
                break
        return {
            "kind": "dynamic_story_action",
            "index": index,
            "actor": actor,
            "text": action,
            "intent": intent,
        }


@dataclass(slots=True)
class StoryPolicyDecision:
    """Policy decision for safety/rule/consistency checks."""

    allowed: bool
    reason: str = ""
    replacement_text: str = ""
    event: dict[str, Any] | None = None


class StorySafetyBoundary:
    """Safety boundary for free-form story actions.

    This component blocks or rewrites unsafe free actions before they reach the
    interpreter and DM. It is deliberately independent from the runner so a
    stricter production policy can replace it.
    """

    def __init__(
        self,
        forbidden_keywords: list[str] | None = None,
        max_action_chars: int = 600,
        replacement_text: str = "观察周围并等待安全时机",
    ) -> None:
        self.forbidden_keywords = [str(item) for item in (forbidden_keywords or [])]
        self.max_action_chars = int(max_action_chars)
        self.replacement_text = replacement_text

    def review_action(self, actor: str, text: str, index: int) -> StoryPolicyDecision:
        """Review raw free action text before interpretation."""
        assert actor, "actor 不能为空"
        action_text = str(text or "").strip()
        if self.max_action_chars > 0 and len(action_text) > self.max_action_chars:
            reason = f"行动文本超过 {self.max_action_chars} 字符"
            return self._blocked(actor, index, reason)
        for keyword in self.forbidden_keywords:
            if keyword and keyword in action_text:
                return self._blocked(actor, index, f"行动包含安全边界关键词: {keyword}")
        return StoryPolicyDecision(allowed=True)

    def _blocked(self, actor: str, index: int, reason: str) -> StoryPolicyDecision:
        """Build a blocked-action decision."""
        return StoryPolicyDecision(
            allowed=False,
            reason=reason,
            replacement_text=self.replacement_text,
            event={
                "kind": "dynamic_story_safety_boundary",
                "index": index,
                "actor": actor,
                "allowed": False,
                "reason": reason,
                "replacement_text": self.replacement_text,
            },
        )


class StoryRuleChecker:
    """Rule checker for interpreted story actions."""

    def __init__(
        self,
        allowed_intents: list[str] | None = None,
        blocked_keywords: list[str] | None = None,
    ) -> None:
        self.allowed_intents = set(str(item) for item in (allowed_intents or []))
        self.blocked_keywords = [str(item) for item in (blocked_keywords or [])]

    def check_action(self, action: dict[str, Any], world: "WorldMemory") -> StoryPolicyDecision:
        """Validate one interpreted action against configured story rules."""
        assert isinstance(action, dict), "action 必须是 dict"
        _ = world
        intent = str(action.get("intent") or "")
        if self.allowed_intents and intent not in self.allowed_intents:
            return self._blocked(action, f"intent '{intent}' 不在允许列表中")
        text = str(action.get("text") or "")
        for keyword in self.blocked_keywords:
            if keyword and keyword in text:
                return self._blocked(action, f"行动包含规则禁用关键词: {keyword}")
        return StoryPolicyDecision(allowed=True)

    @staticmethod
    def _blocked(action: dict[str, Any], reason: str) -> StoryPolicyDecision:
        """Build a blocked rule-check decision."""
        return StoryPolicyDecision(
            allowed=False,
            reason=reason,
            event={
                "kind": "dynamic_story_rule_check",
                "index": action.get("index"),
                "actor": action.get("actor"),
                "allowed": False,
                "reason": reason,
                "intent": action.get("intent"),
            },
        )


class WorldConsistencyChecker:
    """Check rulings against world consistency constraints."""

    def __init__(
        self,
        known_locations: list[str] | None = None,
        allow_unknown_location: bool = True,
        immutable_facts: dict[str, Any] | None = None,
    ) -> None:
        self.known_locations = set(str(item) for item in (known_locations or []))
        self.allow_unknown_location = allow_unknown_location
        self.immutable_facts = dict(immutable_facts or {})

    def check_ruling(self, ruling: dict[str, Any], world: "WorldMemory") -> StoryPolicyDecision:
        """Validate a DM ruling before it is emitted as final story truth."""
        assert isinstance(ruling, dict), "ruling 必须是 dict"
        location = str(ruling.get("location") or "")
        if (
            location
            and location != "unknown"
            and self.known_locations
            and location not in self.known_locations
            and not self.allow_unknown_location
        ):
            return self._blocked(ruling, f"未知地点不允许进入世界状态: {location}")
        for key, expected in self.immutable_facts.items():
            if key in world.state and world.state.get(key) != expected:
                return self._blocked(ruling, f"世界不可变事实被破坏: {key}")
        return StoryPolicyDecision(allowed=True)

    @staticmethod
    def _blocked(ruling: dict[str, Any], reason: str) -> StoryPolicyDecision:
        """Build a consistency violation decision."""
        return StoryPolicyDecision(
            allowed=False,
            reason=reason,
            event={
                "kind": "dynamic_story_consistency_check",
                "index": ruling.get("index"),
                "actor": ruling.get("actor"),
                "allowed": False,
                "reason": reason,
                "location": ruling.get("location"),
            },
        )


class NPCPolicy:
    """Generate NPC reactions from configured keyword triggers."""

    def __init__(self, npcs: list[dict[str, Any]] | None = None) -> None:
        self.npcs = [dict(item) for item in (npcs or []) if isinstance(item, dict)]

    def reactions_for(self, action: dict[str, Any], world: "WorldMemory") -> list[dict[str, Any]]:
        """Return NPC reaction events for one action."""
        assert isinstance(action, dict), "action 必须是 dict"
        _ = world
        text = str(action.get("text") or "")
        result: list[dict[str, Any]] = []
        for npc in self.npcs:
            triggers = [str(item) for item in (npc.get("trigger_keywords") or [])]
            if triggers and not any(keyword in text for keyword in triggers):
                continue
            name = str(npc.get("name") or "NPC")
            response = str(npc.get("response") or f"{name} 注意到了这个行动。")
            result.append({
                "kind": "dynamic_story_npc_reaction",
                "index": action.get("index"),
                "actor": action.get("actor"),
                "npc": name,
                "text": response,
            })
        return result


class DMPolicy:
    """DM adjudication policy."""

    def __init__(self, tone: str = "neutral", consequence_template: str = "") -> None:
        self.tone = tone or "neutral"
        self.consequence_template = consequence_template

    def adjudicate(self, action: dict[str, Any], world: WorldMemory) -> dict[str, Any]:
        """Return a consequence for one interpreted action."""
        if self.consequence_template:
            consequence = self.consequence_template.format(
                actor=action["actor"],
                intent=action["intent"],
                text=action.get("text", ""),
                location=world.state.get("last_location", "unknown"),
                tone=self.tone,
            )
        else:
            consequence = f"DM 裁定（{self.tone}）：{action['actor']} 的 {action['intent']} 行动推进了局势。"
        event = {
            "kind": "dynamic_story_ruling",
            "index": action["index"],
            "actor": action["actor"],
            "intent": action["intent"],
            "consequence": consequence,
            "location": world.state.get("last_location", "unknown"),
        }
        world.remember(event)
        return event


class LlmDmPolicy(DMPolicy):
    """LLM-backed DM policy adapter.

    ``llm_client`` can be any object with ``generate_ruling(prompt, action,
    world)`` or ``complete(prompt)``. Tests and production adapters can inject a
    real LLM client while the runner still depends only on ``DMPolicy``.
    """

    def __init__(
        self,
        llm_client: Any,
        tone: str = "neutral",
        system_prompt: str = "",
        fallback_policy: DMPolicy | None = None,
    ) -> None:
        super().__init__(tone=tone)
        assert llm_client is not None, "llm_client 不能为空"
        self.llm_client = llm_client
        self.system_prompt = system_prompt or "你是动态剧情 DM，请给出简短裁定。"
        self.fallback_policy = fallback_policy or DMPolicy(tone=tone)

    def adjudicate(self, action: dict[str, Any], world: WorldMemory) -> dict[str, Any]:
        """Ask the configured LLM client for a DM ruling."""
        prompt = self._prompt(action, world)
        text = self._call_client(prompt, action, world)
        if not text:
            return self.fallback_policy.adjudicate(action, world)
        event = {
            "kind": "dynamic_story_ruling",
            "index": action["index"],
            "actor": action["actor"],
            "intent": action["intent"],
            "consequence": str(text).strip(),
            "location": world.state.get("last_location", "unknown"),
            "source": "llm_dm",
        }
        world.remember(event)
        return event

    def _prompt(self, action: dict[str, Any], world: WorldMemory) -> str:
        """Build a compact DM prompt for the LLM client."""
        return (
            f"{self.system_prompt}\n"
            f"语气: {self.tone}\n"
            f"世界状态: {world.state}\n"
            f"行动: actor={action.get('actor')} intent={action.get('intent')} text={action.get('text')}\n"
            "请输出裁定结果。"
        )

    def _call_client(self, prompt: str, action: dict[str, Any], world: WorldMemory) -> str:
        """Call a sync LLM client adapter."""
        if hasattr(self.llm_client, "generate_ruling"):
            return str(self.llm_client.generate_ruling(prompt=prompt, action=action, world=world))
        if hasattr(self.llm_client, "complete"):
            return str(self.llm_client.complete(prompt))
        if callable(self.llm_client):
            return str(self.llm_client(prompt))
        raise TypeError("llm_client 必须实现 generate_ruling、complete 或 callable")


class DynamicStoryPolicy:
    """Dynamic story execution policy.

    This component owns actor selection, actor prompts, action interpretation,
    and DM adjudication. The runner keeps lifecycle and event emission only.
    """

    def __init__(
        self,
        action_interpreter: FreeActionInterpreter | None = None,
        dm_policy: DMPolicy | None = None,
        rule_checker: StoryRuleChecker | None = None,
        npc_policy: NPCPolicy | None = None,
        consistency_checker: WorldConsistencyChecker | None = None,
        safety_boundary: StorySafetyBoundary | None = None,
        memory_store: Any = None,
        actor_strategy: str = "round_robin",
        max_context_items: int = 3,
    ) -> None:
        self.action_interpreter = action_interpreter or FreeActionInterpreter()
        self.dm_policy = dm_policy or DMPolicy()
        self.rule_checker = rule_checker or StoryRuleChecker()
        self.npc_policy = npc_policy or NPCPolicy()
        self.consistency_checker = consistency_checker or WorldConsistencyChecker()
        self.safety_boundary = safety_boundary or StorySafetyBoundary()
        self.memory_store = memory_store
        self.actor_strategy = actor_strategy
        self.max_context_items = max(0, int(max_context_items))

    def select_actor(self, state: DynamicStoryState, beat_index: int) -> str:
        """Select the player who should act for this beat."""
        assert state.players, "dynamic_story players 不能为空"
        if self.actor_strategy == "first":
            return state.players[0]
        return state.players[(beat_index - 1) % len(state.players)]

    def select_actors(self, state: DynamicStoryState, beat_index: int, mode: str = "selected") -> list[str]:
        """Select actors who should act for this beat."""
        assert state.players, "dynamic_story players 不能为空"
        if mode == "all_players":
            return list(state.players)
        return [self.select_actor(state, beat_index)]

    def perception_for(self, state: DynamicStoryState, world: WorldMemory) -> dict[str, Any]:
        """Build the actor perception event for the current world."""
        long_term_context = self._long_term_context(state.world_name)
        memory_text = ""
        if long_term_context:
            memory_text = "；长期记忆：" + " / ".join(
                str(item.get("text") or item.get("consequence") or item)
                for item in long_term_context
            )
        return {
            "scope": "dynamic_story",
            "sender": "dm",
            "text": (
                f"世界：{state.world_name}；前提：{state.premise}；"
                f"当前世界状态：{world.state}"
                f"{memory_text}"
            ),
        }

    def cue_for(self, actor_name: str, beat_index: int, action_hint: str) -> str:
        """Build the actor action cue for one story beat."""
        return (
            f"现在是动态剧情第 {beat_index} 个 beat。"
            f"请以 {actor_name} 的身份提出一个自由行动。"
            f"行动提示：{action_hint or '根据当前局势行动'}。"
            "只输出行动文本。"
        )

    def interpret_action(self, actor_name: str, text: str, index: int) -> dict[str, Any]:
        """Interpret free action text into a structured event."""
        return self.action_interpreter.interpret(actor_name, text, index)

    def review_raw_action(self, actor_name: str, text: str, index: int) -> StoryPolicyDecision:
        """Review raw action text before interpretation."""
        return self.safety_boundary.review_action(actor_name, text, index)

    def check_action_rules(self, action: dict[str, Any], world: WorldMemory) -> StoryPolicyDecision:
        """Check interpreted action against story rules."""
        return self.rule_checker.check_action(action, world)

    def npc_reactions(self, action: dict[str, Any], world: WorldMemory) -> list[dict[str, Any]]:
        """Return NPC reactions for an accepted action."""
        return self.npc_policy.reactions_for(action, world)

    def adjudicate(self, action: dict[str, Any], world: WorldMemory) -> dict[str, Any]:
        """Adjudicate an interpreted action and update world memory."""
        return self.dm_policy.adjudicate(action, world)

    def check_world_consistency(self, ruling: dict[str, Any], world: WorldMemory) -> StoryPolicyDecision:
        """Check a ruling against world consistency constraints."""
        return self.consistency_checker.check_ruling(ruling, world)

    def _long_term_context(self, world_name: str) -> list[dict[str, Any]]:
        """Recall long-term memories for this world."""
        if self.memory_store is None or not hasattr(self.memory_store, "recall_long_term"):
            return []
        return self.memory_store.recall_long_term(f"dynamic_story:{world_name}", limit=self.max_context_items)

__all__ = [
    "DMPolicy",
    "DynamicStoryPolicy",
    "FreeActionInterpreter",
    "LlmDmPolicy",
    "NPCPolicy",
    "StoryPolicyDecision",
    "StoryRuleChecker",
    "StorySafetyBoundary",
    "WorldConsistencyChecker",
]
