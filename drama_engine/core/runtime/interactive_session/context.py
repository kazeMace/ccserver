"""Runtime execution context for interactive_session."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from typing import Any

from drama_engine.core.dsl.components import CandidateResolver, ConditionEvaluator, EffectExecutor, ValueResolver
from drama_engine.core.engine import Cast, State, StateWriter
from drama_engine.core.executor import ExecutorRegistry
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
    executor_registry: ExecutorRegistry | None
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
    # 惰性缓存的信息隔离层（按 script.visibility 构建），供 project_for_actor 复用。
    _firewall: Any = None
    # 进度回调：flow/scene 推进时调用 on_progress(current_state, current_scene, round)，
    # 由 runner 接到 SessionState.progress（M5.2）。None 时不追踪（最小 runtime 兼容）。
    on_progress: Any = None

    def notify_progress(self) -> None:
        """把当前 flow/scene 位置与轮次上报给进度回调（若已接线）。"""
        if self.on_progress is None:
            return
        game_round = self.state.get_attr("GAME", "round") if self.state is not None else None
        self.on_progress(
            current_state=self.current_state_id or None,
            current_scene=self.current_scene_id or None,
            round=int(game_round) if isinstance(game_round, int) else None,
        )

    def runtime_extra(self) -> dict[str, Any]:
        """Build common extra context for executors."""
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
        """Build executor extra data that may call back into this runtime.

        Args:
            **items: Extra event/hook-specific context.

        Returns:
            Dict with normal runtime context plus a non-serializable runtime
            pointer for async inside executors.
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

    def project_for_actor(self, actor_name: str, purpose: str = "prompt") -> dict[str, Any]:
        """为指定 actor 生成受限上下文投影（KnowledgeFirewall）。

        结合三层可见性：
          - 静态：script.visibility.secret_attrs（他人秘密属性遮蔽）。
          - 动态：disclosure_ledger.facts_for(actor)（已被披露的事实，如验人结果）。
          - 授权：actor 视角始终是 restricted（不给全局 state）。

        参数：
          actor_name — 目标 actor 名（seat_id）。
          purpose    — 投影用途，默认 "prompt"。

        返回：受限上下文 dict（含 self / others / game / disclosed）。
        """
        # 惰性构建 firewall：秘密属性来自编译后的 visibility 策略。
        if self._firewall is None:
            from drama_engine.core.visibility.knowledge_firewall import (
                build_knowledge_firewall_from_policy,
            )
            policy = getattr(self.script, "visibility", None)
            self._firewall = build_knowledge_firewall_from_policy(policy)
        disclosed = None
        if self.disclosure_ledger is not None and actor_name:
            disclosed = self.disclosure_ledger.facts_for(actor_name)
        return self._firewall.project_context(
            state=self.state,
            audience=f"agent:{actor_name}",
            purpose=purpose,
            disclosed_facts=disclosed,
        )

    def resolve_guardrail(self) -> Any:
        """返回当前 scene 生效的 OOC 内容守卫 GuardRail；未启用时返回 None。

        优先取当前 scene 的 guardrail 声明，未启用则回退到全局 guardrail。
        构建结果不缓存（scene 会切换），但仅在启用时才创建实例。
        """
        from drama_engine.core.moderation.guardrail import build_guardrail

        scene = self.script.scenes.get(self.current_scene_id) if self.script else None
        scene_spec = getattr(scene, "guardrail", None) if scene is not None else None
        if scene_spec is not None and getattr(scene_spec, "enabled", False):
            return build_guardrail(scene_spec)
        global_spec = getattr(self.script, "guardrail", None) if self.script else None
        return build_guardrail(global_spec)
