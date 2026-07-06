"""Participant action executor."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.candidate_validation import (
    CandidateResponseValidator,
)
from drama_engine.core.runtime.interactive_session.actions.response_models import ResponseModelFactory
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.models import ParticipantActionSpec, ScopeSpec
from drama_engine.core.runtime.interactive_session.scene.scope import ScopeResolver


logger = logging.getLogger(__name__)


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
        candidates = await self._resolve_candidates(ctx, action, actor_name)
        if hasattr(actor, "set_candidates"):
            actor.set_candidates(candidates)
        if hasattr(actor, "set_scene_context"):
            actor.set_scene_context(ctx.current_scene_id, ctx.current_scene_id)
        collect_model = self._models.build(action.kind, action.response, action.target)
        prompt = self._build_prompt(cue, action, candidates, ctx, actor_name)
        response = await self._act_with_validation(
            actor=actor,
            prompt=prompt,
            collect_model=collect_model,
            candidates=candidates,
            scene_id=ctx.current_scene_id,
        )
        response.setdefault("actor", actor_name)
        response.setdefault("data", None)
        # OOC 内容守卫：发言写入前判定是否越界/离题/泄密，按策略处理。
        response = await self._apply_guardrail(ctx, response)
        if response is None:
            # 被 block 策略拦截：不投递，返回一个空发言占位（不进入他人感知/事件流）。
            logger.info("[ParticipantActionExecutor] 发言被 GuardRail 拦截 actor=%s", actor_name)
            return {"actor": actor_name, "text": "", "data": None, "blocked": True}
        await self._deliver_response(ctx, response, scope, participants)
        return response

    async def _apply_guardrail(
        self,
        ctx: InteractiveExecutionContext,
        response: dict[str, Any],
    ) -> dict[str, Any] | None:
        """对发言应用 OOC 内容守卫。

        返回：
          - 放行：返回（可能被改写过的）发言 dict。
          - 拦截：返回 None。
        守卫未启用或判定异常时原样放行。
        """
        guardrail = ctx.resolve_guardrail()
        if guardrail is None or not guardrail.enabled:
            return response
        outcome = await guardrail.check(ctx, response)
        # 被打标时给 host 发一条观测事件（不影响投递）。
        if outcome.flagged:
            ctx.emit_host({
                "kind": "guardrail_flag",
                "runtime_type": "interactive_session",
                "scene": ctx.current_scene_id,
                "actor": response.get("actor"),
                "allow": outcome.allow,
                "note": outcome.note,
            })
        if not outcome.allow:
            return None
        return outcome.response

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
            logger.warning(
                "[InteractiveCandidateValidation:%s] 第 %s 次候选校验失败: %s",
                getattr(actor, "name", "?"),
                attempt + 1,
                error,
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

    async def _resolve_candidates(
        self,
        ctx: InteractiveExecutionContext,
        action: ParticipantActionSpec,
        actor_name: str,
    ) -> list[str]:
        """Resolve action candidates for one actor."""
        if not action.candidates:
            return []
        try:
            return await ctx.candidate_resolver.resolve_async(
                action.candidates,
                ctx.state,
                ctx.last_responses,
                actor=actor_name,
                extra=ctx.condition_extra(),
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
        ctx: InteractiveExecutionContext | None = None,
        actor_name: str = "",
    ) -> str:
        """Build the task prompt sent to actor.

        通过 KnowledgeFirewall 为该 actor 生成受限上下文投影（自己的属性 + 已披露事实），
        并入 prompt。firewall 保证：只给该 actor「自己天生该知道 + 被告知过」的信息，
        绝不泄露他人秘密属性——因此这是安全的「加法」，不会造成剧透/开天眼。
        """
        parts = []
        if cue:
            parts.append(cue)
        if action.cue and str(action.cue) not in parts:
            parts.append(str(action.cue))
        if candidates:
            parts.append("候选项：" + "、".join(self._candidate_labels(candidates)))
        # 注入 firewall 受限投影：仅当 ctx/actor 就绪时启用。
        knowledge = self._build_actor_knowledge(ctx, actor_name)
        if knowledge:
            parts.append(knowledge)
        prompt = "\n".join(parts).strip()
        return prompt or "请根据当前场景行动。"

    def _build_actor_knowledge(
        self,
        ctx: InteractiveExecutionContext | None,
        actor_name: str,
    ) -> str:
        """把 firewall 受限投影渲染成一段「你已知的信息」文本，供并入 prompt。

        无 ctx / actor 时返回空串（不影响原有 prompt）。只渲染 self 私有属性与
        已披露事实这两类「该 actor 独有」的信息；公开信息由感知缓冲的场上消息承载。
        """
        if ctx is None or not actor_name:
            return ""
        try:
            view = ctx.project_for_actor(actor_name, purpose="prompt")
        except Exception as exc:  # noqa: BLE001 - 投影失败不应中断发言，仅记录告警。
            logger.warning("[ParticipantActionExecutor] firewall 投影失败 actor=%s: %s", actor_name, exc)
            return ""
        lines: list[str] = []
        self_attrs = view.get("self") or {}
        if self_attrs:
            pairs = "、".join(f"{key}={value}" for key, value in self_attrs.items())
            lines.append(f"你的身份与状态：{pairs}")
        disclosed = view.get("disclosed") or {}
        if disclosed:
            pairs = "；".join(f"{ref}：{value}" for ref, value in disclosed.items())
            lines.append(f"你已获知的信息：{pairs}")
        if not lines:
            return ""
        return "【你已知的信息】\n" + "\n".join(lines)

    def _candidate_labels(self, candidates: list) -> list[str]:
        """把候选项渲染为可读文本，兼容字符串候选与 {id,text} 结构候选。"""
        labels: list[str] = []
        for item in candidates:
            if isinstance(item, dict):
                labels.append(str(item.get("text") or item.get("id") or item))
            else:
                labels.append(str(item))
        return labels

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
            private_event = {**event, "visibility": "private"}
            if ctx.emit_private is not None:
                for name in members:
                    ctx.emit_private(str(name), private_event)
            ctx.emit_host(private_event)
