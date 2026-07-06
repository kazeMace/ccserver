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
        timeout_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """Collect responses according to a basic execution mode."""
        if action.kind in {"none", "narration"} or not actor_names:
            return []
        if mode == "simultaneous":
            return await self._collect_simultaneous(
                ctx,
                actor_names,
                action,
                scope,
                participants,
                cue,
                timeout_ms,
            )
        responses = []
        for name in actor_names:
            response = await self._collect_one_with_timeout(
                ctx,
                name,
                action,
                scope,
                participants,
                cue,
                timeout_ms,
            )
            if response is not None:
                responses.append(response)
        return responses

    async def _collect_simultaneous(
        self,
        ctx: InteractiveExecutionContext,
        actor_names: list[str],
        action: ParticipantActionSpec,
        scope: ScopeSpec,
        participants: list[str],
        cue: str,
        timeout_ms: int | None,
    ) -> list[dict[str, Any]]:
        """Collect simultaneous responses and cancel actors that exceed timeout."""
        tasks = [
            asyncio.create_task(self.collect_one(ctx, name, action, scope, participants, cue))
            for name in actor_names
        ]
        timeout_seconds = self._timeout_seconds(timeout_ms)
        done, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
        if pending:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            timed_out = [
                actor_names[index]
                for index, task in enumerate(tasks)
                if task in pending
            ]
            self._emit_timeout(ctx, timed_out, timeout_ms)
        responses = []
        for task in tasks:
            if task in done:
                responses.append(task.result())
        return responses

    async def _collect_one_with_timeout(
        self,
        ctx: InteractiveExecutionContext,
        actor_name: str,
        action: ParticipantActionSpec,
        scope: ScopeSpec,
        participants: list[str],
        cue: str,
        timeout_ms: int | None,
    ) -> dict[str, Any] | None:
        """Collect one actor response with optional timeout."""
        timeout_seconds = self._timeout_seconds(timeout_ms)
        try:
            if timeout_seconds is None:
                return await self.collect_one(ctx, actor_name, action, scope, participants, cue)
            return await asyncio.wait_for(
                self.collect_one(ctx, actor_name, action, scope, participants, cue),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            self._emit_timeout(ctx, [actor_name], timeout_ms)
            return None

    def _timeout_seconds(self, timeout_ms: int | None) -> float | None:
        """Convert timeout milliseconds to seconds."""
        if timeout_ms is None:
            return None
        timeout = max(0, int(timeout_ms)) / 1000
        return timeout

    def _emit_timeout(
        self,
        ctx: InteractiveExecutionContext,
        actor_names: list[str],
        timeout_ms: int | None,
    ) -> None:
        """Emit a host-visible actor timeout warning."""
        ctx.emit_host({
            "kind": "interactive_schedule_timeout",
            "scene": ctx.current_scene_id,
            "actors": list(actor_names),
            "timeout_ms": timeout_ms,
            "message": "schedule.timeout_ms 超时，已跳过未完成 actor",
        })

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
