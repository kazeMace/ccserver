"""Participant action executor."""

from __future__ import annotations

import asyncio
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.candidate_validation import (
    CandidateResponseValidator,
)
from drama_engine.core.runtime.interactive_session.actions.response_models import ResponseModelFactory
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.models import ParticipantActionSpec, ScopeSpec
from drama_engine.core.runtime.interactive_session.scene.scope import ScopeResolver


class ParticipantActionExecutor:
    """Collect actions from scheduled participants."""

    MAX_COLLECT_RETRIES = 3

    def __init__(self, scope_resolver: ScopeResolver | None = None) -> None:
        """Initialize executor."""
        self._models = ResponseModelFactory()
        self._scope_resolver = scope_resolver or ScopeResolver()
        self._candidate_validator = CandidateResponseValidator()

    async def collect_one(
        self,
        ctx: InteractiveExecutionContext,
        actor_name: str,
        action: ParticipantActionSpec,
        scope: ScopeSpec,
        participants: list[str],
        cue: str = "",
    ) -> dict[str, Any]:
        """Collect one actor response and deliver visible message."""
        actor = ctx.cast.get(actor_name)
        candidates = self._resolve_candidates(ctx, action, actor_name)
        if hasattr(actor, "set_candidates"):
            actor.set_candidates(candidates)
        if hasattr(actor, "set_scene_context"):
            actor.set_scene_context(ctx.current_scene_id, ctx.current_scene_id)
        collect_model = self._models.build(action.kind, action.response, action.target)
        prompt = self._build_prompt(cue, action, candidates)
        response = await self._act_with_validation(
            actor=actor,
            prompt=prompt,
            collect_model=collect_model,
            candidates=candidates,
            scene_id=ctx.current_scene_id,
        )
        response.setdefault("actor", actor_name)
        response.setdefault("data", None)
        await self._deliver_response(ctx, response, scope, participants)
        return response

    async def _act_with_validation(
        self,
        actor: Any,
        prompt: str,
        collect_model: Any,
        candidates: list[str],
        scene_id: str,
    ) -> dict[str, Any]:
        """Collect one response and enforce candidate boundaries."""
        current_prompt = prompt
        last_error = ""
        for attempt in range(self.MAX_COLLECT_RETRIES):
            response = await actor.act(current_prompt, collect_model)
            error = self._candidate_validator.validate(response, candidates, scene_id)
            if not error:
                return response
            last_error = error
            print(
                f"[InteractiveCandidateValidation:{getattr(actor, 'name', '?')}] "
                f"第 {attempt + 1} 次候选校验失败: {error}"
            )
            current_prompt = (
                prompt
                + "\n\n【上次输出无效】"
                + error
                + "请从候选项中重新选择，并保持输出格式不变。"
            )
        raise ValueError(last_error or "候选校验失败")

    async def collect_many(
        self,
        ctx: InteractiveExecutionContext,
        actor_names: list[str],
        action: ParticipantActionSpec,
        scope: ScopeSpec,
        participants: list[str],
        mode: str,
        cue: str = "",
    ) -> list[dict[str, Any]]:
        """Collect responses according to a basic execution mode."""
        if action.kind in {"none", "narration"} or not actor_names:
            return []
        if mode == "simultaneous":
            return list(await asyncio.gather(*[
                self.collect_one(ctx, name, action, scope, participants, cue)
                for name in actor_names
            ]))
        responses = []
        for name in actor_names:
            responses.append(await self.collect_one(ctx, name, action, scope, participants, cue))
        return responses

    def _resolve_candidates(
        self,
        ctx: InteractiveExecutionContext,
        action: ParticipantActionSpec,
        actor_name: str,
    ) -> list[str]:
        """Resolve action candidates for one actor."""
        if not action.candidates:
            return []
        try:
            return ctx.candidate_resolver.resolve(
                action.candidates,
                ctx.state,
                ctx.last_responses,
                actor=actor_name,
            )
        except Exception as exc:  # noqa: BLE001 - candidate failure should be visible.
            ctx.emit_host({
                "kind": "interactive_session_warning",
                "message": f"候选集解析失败: {exc}",
                "scene": ctx.current_scene_id,
            })
            if action.target == "required" or action.kind in {"vote", "choose"}:
                raise ValueError(f"候选集解析失败，无法执行 {action.kind}: {exc}") from exc
            return []

    def _build_prompt(
        self,
        cue: str,
        action: ParticipantActionSpec,
        candidates: list[str],
    ) -> str:
        """Build the task prompt sent to actor."""
        parts = []
        if cue:
            parts.append(cue)
        if action.cue and str(action.cue) not in parts:
            parts.append(str(action.cue))
        if candidates:
            parts.append("候选项：" + "、".join(candidates))
        prompt = "\n".join(parts).strip()
        return prompt or "请根据当前场景行动。"

    async def _deliver_response(
        self,
        ctx: InteractiveExecutionContext,
        response: dict[str, Any],
        scope: ScopeSpec,
        participants: list[str],
    ) -> None:
        """Deliver one response to allowed actors and public event streams."""
        sender = str(response.get("actor") or "")
        text = str(response.get("text") or "")
        if not text:
            return
        all_names = ctx.cast.all_names()
        members = self._scope_resolver.members(scope, all_names, participants)
        event = {
            "kind": "interactive_message",
            "runtime_type": "interactive_session",
            "scene": ctx.current_scene_id,
            "scope": scope.id,
            "sender": sender,
            "text": text,
        }
        for name in members:
            if name != sender:
                await ctx.cast.get(name).perceive(event)
        if scope.visibility == "public":
            ctx.emit_public(event)
        else:
            ctx.emit_host({**event, "visibility": "private"})
