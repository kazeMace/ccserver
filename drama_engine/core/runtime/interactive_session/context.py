"""Runtime execution context for interactive_session."""

from __future__ import annotations

from dataclasses import dataclass, field
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
    last_responses: list[dict[str, Any]] = field(default_factory=list)
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
            "metadata": self.session_metadata,
        }

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
            "current_state": self.current_state_id,
            "current_scene": self.current_scene_id,
            "last_responses": list(self.last_responses),
            "patches": self.patch_journal.snapshot(),
            "metadata": dict(self.session_metadata),
        }
