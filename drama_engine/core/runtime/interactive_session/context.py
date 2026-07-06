"""Runtime execution context for interactive_session."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from typing import Any

from drama_engine.core.dsl.components import CandidateResolver, ConditionEvaluator, EffectExecutor, ValueResolver
from drama_engine.core.engine import Cast, State, StateWriter
from drama_engine.core.runtime.interactive_session.models import InteractiveScript
from drama_engine.core.runtime.interactive_session.patch.journal import PatchJournal


@dataclass(slots=True)
class InteractiveExecutionContext:
    """Shared execution dependencies for all interactive_session executors."""

    script: InteractiveScript
    state: State
    writer: StateWriter
    cast: Cast
    condition_evaluator: ConditionEvaluator
    effect_executor: EffectExecutor
    candidate_resolver: CandidateResolver
    value_resolver: ValueResolver
    plugin_registry: Any
    patch_journal: PatchJournal
    emit_public: Any
    emit_host: Any
    session_metadata: dict[str, Any]
    emit_private: Any = None
    # 披露账本：记录「谁被告知了哪条动态事实」，供 KnowledgeFirewall 合成 actor view。
    # None 时 record_disclosure 静默跳过（兼容不需要披露账本的最小 runtime）。
    disclosure_ledger: Any = None
    base_raw: dict[str, Any] = field(default_factory=dict)
    last_responses: list[dict[str, Any]] = field(default_factory=list)
    message_history: list[dict[str, Any]] = field(default_factory=list)
    current_state_id: str = ""
    current_scene_id: str = ""
    ended: bool = False
    result: str | None = None

    def runtime_extra(self) -> dict[str, Any]:
        """Build common extra context for evaluators."""
        return {
            "__state": self.state,
            "current_state": self.current_state_id,
            "current_scene": self.current_scene_id,
            "patch_journal": self.patch_journal.snapshot(),
            "metadata": self.serializable_metadata(),
            "base_flow": deepcopy(self.base_raw),
            "messages": deepcopy(self.message_history),
            "players": list(self.state.get_attr("GAME", "players", []) or []),
            "participants": list(self.session_metadata.get("interactive_current_participants") or []),
        }

    def serializable_metadata(self) -> dict[str, Any]:
        """Return session metadata that is safe for JSON service payloads.

        Runtime-only handles such as Agent/client objects stay in
        `session_metadata` for direct Python calls, but they must not leak into
        prompts, HTTP bodies, or journal-like payloads.
        """
        hidden_keys = {
            "inside_agent",
            "llm_client",
            "llm_provider",
            "__interactive_inside_agent",
        }
        result: dict[str, Any] = {}
        for key, value in self.session_metadata.items():
            if key in hidden_keys:
                continue
            try:
                result[key] = json.loads(json.dumps(value, ensure_ascii=False))
            except (TypeError, ValueError):
                continue
        return result

    def condition_extra(self, **items: Any) -> dict[str, Any]:
        """Build evaluator extra data that may call back into this runtime.

        Args:
            **items: Extra event/hook-specific context.

        Returns:
            Dict with normal runtime context plus a non-serializable runtime
            pointer for async inside evaluators.
        """
        result = self.runtime_extra()
        result["__interactive_ctx"] = self
        for key in ("inside_agent", "llm_client", "llm_provider", "inside_agent_id"):
            if key in self.session_metadata:
                result[key] = self.session_metadata[key]
        result.update(items)
        return result

    def full_context_payload(self) -> dict[str, Any]:
        """Return a serializable runtime payload for external services.

        Returns:
            Dict containing state snapshot, current location, responses, patches,
            players, and metadata. This is used when DSL omits explicit input.
        """
        return {
            "runtime_type": "interactive_session",
            "state": self.state.snapshot(),
            "players": list(self.state.get_attr("GAME", "players", []) or []),
            "participants": list(self.session_metadata.get("interactive_current_participants") or []),
            "current_state": self.current_state_id,
            "current_scene": self.current_scene_id,
            "last_responses": list(self.last_responses),
            "messages": deepcopy(self.message_history),
            "patches": self.patch_journal.snapshot(),
            "metadata": self.serializable_metadata(),
            "base_flow": deepcopy(self.base_raw),
        }

    def record_message(self, event: dict[str, Any]) -> None:
        """Append a serializable message-like event to runtime history."""
        try:
            item = json.loads(json.dumps(event, ensure_ascii=False))
        except (TypeError, ValueError):
            return
        self.message_history.append(item)

    def record_disclosure(self, actor: str, fact_ref: str, value: Any) -> None:
        """把一条披露记录写入披露账本（若已挂载）。

        参数：
          actor    — 被披露的对象（seat_id / actor 名）。
          fact_ref — 事实引用键（如 "GAME.last_inspection_result"）。
          value    — 披露的具体值。
        当 disclosure_ledger 为 None 时静默跳过。at_beat 取当前 GAME.round。
        """
        if self.disclosure_ledger is None:
            return
        if not actor or not fact_ref:
            return
        at_beat = int(self.state.get_attr("GAME", "round", 0) or 0)
        self.disclosure_ledger.record(actor, fact_ref, value, at_beat=at_beat)
