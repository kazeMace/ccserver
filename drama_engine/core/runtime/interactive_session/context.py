"""Runtime execution context for interactive_session."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from typing import Any

from drama_engine.core.components import CandidateResolver, ConditionEvaluator, EffectExecutor, ValueResolver
from drama_engine.core.engine import Cast, State, StateWriter
from drama_engine.core.executor import ExecutorRegistry
from drama_engine.core.runtime.interactive_session.models import InteractiveScript
from drama_engine.core.runtime.interactive_session.patch.journal import PatchJournal


@dataclass(slots=True)
class RuntimeServices:
    """运行时服务依赖 — 不可变，session 生命周期内固定。

    由 Runner.assign() 构建，注入到 ctx 中。
    """

    condition_evaluator: ConditionEvaluator
    effect_executor: EffectExecutor
    candidate_resolver: CandidateResolver
    value_resolver: ValueResolver
    executor_registry: ExecutorRegistry | None
    plugin_registry: Any


@dataclass(slots=True)
class RuntimeEmitters:
    """事件发射器集合 — session 级不可变。"""

    emit_public: Any
    emit_host: Any
    emit_private: Any = None


@dataclass
class InteractiveExecutionContext:
    """运行时执行上下文 — 分层组合。

    数据流：
      ScriptBundle → Runner.assign() → InteractiveExecutionContext
                                              ├── .services   (服务依赖，不可变)
                                              ├── .emitters   (事件发射，不可变)
                                              └── 可变运行时状态
    """

    # 编译产物（静态）
    script: InteractiveScript

    # 分层：服务依赖（不可变）
    services: RuntimeServices

    # 分层：事件发射器（不可变）
    emitters: RuntimeEmitters

    # 可变运行时状态
    state: State
    writer: StateWriter
    cast: Cast
    patch_journal: PatchJournal
    session_metadata: dict[str, Any]
    disclosure_ledger: Any = None
    base_raw: dict[str, Any] = field(default_factory=dict)
    last_responses: list[dict[str, Any]] = field(default_factory=list)
    message_history: list[dict[str, Any]] = field(default_factory=list)
    current_state_id: str = ""
    current_scene_id: str = ""
    ended: bool = False
    result: str | None = None
    _firewall: Any = None
    on_progress: Any = None
    hook_runner: Any = None

    # ─── 向后兼容属性（逐步废弃直接访问） ──────────────────

    @property
    def condition_evaluator(self) -> ConditionEvaluator:
        return self.services.condition_evaluator

    @property
    def effect_executor(self) -> EffectExecutor:
        return self.services.effect_executor

    @property
    def candidate_resolver(self) -> CandidateResolver:
        return self.services.candidate_resolver

    @property
    def value_resolver(self) -> ValueResolver:
        return self.services.value_resolver

    @property
    def executor_registry(self) -> ExecutorRegistry | None:
        return self.services.executor_registry

    @property
    def plugin_registry(self) -> Any:
        return self.services.plugin_registry

    @property
    def emit_public(self) -> Any:
        return self.emitters.emit_public

    @property
    def emit_host(self) -> Any:
        return self.emitters.emit_host

    @property
    def emit_private(self) -> Any:
        return self.emitters.emit_private

    # ─── 方法 ──────────────────────────────────────────────

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
        """Return session metadata that is safe for JSON service payloads."""
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
        """Build executor extra data that may call back into this runtime."""
        result = self.runtime_extra()
        result["__interactive_ctx"] = self
        for key in ("inside_agent", "llm_client", "llm_provider", "inside_agent_id"):
            if key in self.session_metadata:
                result[key] = self.session_metadata[key]
        result.update(items)
        return result

    def full_context_payload(self) -> dict[str, Any]:
        """Return a serializable runtime payload for external services."""
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
        """把一条披露记录写入披露账本（若已挂载）。"""
        if self.disclosure_ledger is None:
            return
        if not actor or not fact_ref:
            return
        at_beat = int(self.state.get_attr("GAME", "round", 0) or 0)
        self.disclosure_ledger.record(actor, fact_ref, value, at_beat=at_beat)

    def project_for_actor(self, actor_name: str, purpose: str = "prompt") -> dict[str, Any]:
        """为指定 actor 生成受限上下文投影（KnowledgeFirewall）。"""
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
        """返回当前 scene 生效的 OOC 内容守卫 GuardRail；未启用时返回 None。"""
        from drama_engine.core.moderation.guardrail import build_guardrail

        scene = self.script.scenes.get(self.current_scene_id) if self.script else None
        scene_spec = getattr(scene, "guardrail", None) if scene is not None else None
        if scene_spec is not None and getattr(scene_spec, "enabled", False):
            return build_guardrail(scene_spec)
        global_spec = getattr(self.script, "guardrail", None) if self.script else None
        return build_guardrail(global_spec)


__all__ = ["InteractiveExecutionContext", "RuntimeEmitters", "RuntimeServices"]
