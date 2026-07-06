"""HTTP and LLM condition evaluator."""

from __future__ import annotations

import json
import os
import inspect
import asyncio
import threading
import urllib.error
import urllib.request
from typing import Any, Callable

from drama_engine.core.dsl.components.interaction_protocol import InteractionProtocolBuilder
from drama_engine.core.dsl.components.service_input import ServiceInputBuilder
from drama_engine.core.engine import State


class ExternalConditionEvaluator:
    """Evaluate conditions by calling configured HTTP/LLM endpoints."""

    def __init__(self, evaluate_condition: Callable, resolve_value_expr: Callable):
        """
        Initialize the external evaluator.

        Args:
            evaluate_condition: Callback used to evaluate pass_when.
            resolve_value_expr: Callback used to resolve evaluator input refs.
        """
        self._evaluate = evaluate_condition
        self._resolve_value_expr = resolve_value_expr
        self._input_builder = ServiceInputBuilder()
        self._protocol = InteractionProtocolBuilder()

    def evaluate(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """Evaluate an `evaluator: http` or `evaluator: llm` condition."""
        url = self._resolve_evaluator_url(cond)
        if not url and cond.get("evaluator") == "llm" and str(cond.get("provider") or "inside") == "inside":
            client_result = self._call_inside_client(cond, state, actor, candidate, responses, extra, entity)
            if client_result is not None:
                return self._result_passes(cond, client_result, state, actor, candidate, responses, extra, entity)
            result = {
                "result": bool(cond.get("fallback", False)),
                "provider": "inside",
                "input": self._build_input(
                    cond,
                    self._default_input(state, actor, candidate, responses, extra, entity),
                    state,
                    actor,
                    candidate,
                    responses,
                    extra,
                    entity,
                ),
            }
            pass_when = cond.get("pass_when")
            if isinstance(pass_when, dict):
                return self._evaluate(
                    pass_when,
                    state=state,
                    actor=actor,
                    candidate=candidate,
                    responses=responses,
                    extra={**(extra or {}), "result": result},
                    entity=entity,
                )
            return bool(result["result"])
        if not url:
            return bool(cond.get("fallback", False))
        default_input = self._default_input(state, actor, candidate, responses, extra, entity)
        input_payload = self._build_input(cond, default_input, state, actor, candidate, responses, extra, entity)
        payload = self._protocol.with_legacy_aliases(
            self._condition_envelope(
                cond,
                input_payload,
                default_input,
                actor,
                candidate,
                entity,
                responses,
                extra,
                provider=str(cond.get("provider") or cond.get("evaluator") or "http"),
            )
        )
        timeout = int(cond.get("timeout_ms") or 3000) / 1000
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={**dict(cond.get("headers") or {}), "Content-Type": "application/json"},
            method=str(cond.get("method") or "POST").upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return bool(cond.get("fallback", False))

        return self._result_passes(cond, result, state, actor, candidate, responses, extra, entity)

    async def evaluate_async(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """Evaluate an external condition from an async runtime path."""
        if cond.get("evaluator") == "llm" and str(cond.get("provider") or "inside") == "inside":
            client_result = await self._call_inside_client_async(
                cond,
                state,
                actor,
                candidate,
                responses,
                extra,
                entity,
            )
            if client_result is not None:
                return self._result_passes(cond, client_result, state, actor, candidate, responses, extra, entity)
        return await asyncio.to_thread(
            self.evaluate,
            cond,
            state,
            actor,
            candidate,
            responses,
            extra,
            entity,
        )

    def _result_passes(
        self,
        cond: dict,
        result: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """Evaluate an external result payload."""
        confidence = result.get("confidence")
        min_confidence = cond.get("min_confidence")
        if (
            min_confidence is not None
            and confidence is not None
            and float(confidence) < float(min_confidence)
        ):
            return bool(cond.get("fallback", False))
        pass_when = cond.get("pass_when")
        if isinstance(pass_when, dict):
            return self._evaluate(
                pass_when,
                state=state,
                actor=actor,
                candidate=candidate,
                responses=responses,
                extra={**(extra or {}), "result": result},
                entity=entity,
            )
        if "result" in result:
            return bool(result["result"])
        if "passed" in result:
            return bool(result["passed"])
        if "ended" in result:
            return bool(result["ended"])
        return bool(cond.get("fallback", False))

    def _call_inside_client(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> dict | None:
        """Call an inside LLM/Agent client from a sync compatibility path."""
        runtime_ctx = (extra or {}).get("__interactive_ctx")
        metadata = (extra or {}).get("metadata") or {}
        client = (
            cond.get("client")
            or (extra or {}).get("inside_agent")
            or (extra or {}).get("llm_client")
            or metadata.get("inside_agent")
            or metadata.get("llm_client")
            or metadata.get("llm_provider")
        )
        if client is None and runtime_ctx is not None:
            try:
                from drama_engine.core.runtime.interactive_session.services.inside_agent import (
                    InsideAgentFactory,
                )

                client = InsideAgentFactory().get_or_create(runtime_ctx.session_metadata, cond)
            except Exception:  # noqa: BLE001 - keep sync evaluator deterministic.
                client = None
        if client is None:
            return None
        payload = self._build_input(
            cond,
            self._default_input(state, actor, candidate, responses, extra, entity),
            state,
            actor,
            candidate,
            responses,
            extra,
            entity,
        )
        prompt = cond.get("prompt") or json.dumps(
            self._condition_envelope(
                cond,
                payload,
                self._default_input(state, actor, candidate, responses, extra, entity),
                actor,
                candidate,
                entity,
                responses,
                extra,
                provider="inside",
            ),
            ensure_ascii=False,
        )
        if hasattr(client, "run"):
            value = client.run(str(prompt))
        elif hasattr(client, "act"):
            value = client.act(str(prompt), None)
        elif hasattr(client, "generate_ruling"):
            value = client.generate_ruling(prompt=prompt, action=cond.get("semantic_id"), world=payload)
        elif hasattr(client, "complete"):
            value = client.complete(prompt)
        elif callable(client):
            value = client({"condition": cond, "payload": payload})
        else:
            return None
        if inspect.isawaitable(value):
            value = self._await_sync(value)
        if isinstance(value, dict) and "text" in value and "data" in value:
            data = value.get("data")
            if isinstance(data, dict):
                return data
        return self._decode_inside_value(value)

    def _await_sync(self, value: Any) -> Any:
        """Wait for an awaitable when a legacy sync caller invokes inside."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(value)
        result: dict[str, Any] = {}

        def run_in_thread() -> None:
            try:
                result["value"] = asyncio.run(value)
            except BaseException as exc:  # noqa: BLE001 - re-raise in caller thread.
                result["error"] = exc

        thread = threading.Thread(target=run_in_thread, daemon=True)
        thread.start()
        thread.join()
        if "error" in result:
            raise result["error"]
        return result.get("value")

    async def _call_inside_client_async(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> dict | None:
        """Call an injected or runtime ccserver Agent for inside LLM conditions."""
        runtime_ctx = (extra or {}).get("__interactive_ctx")
        metadata = (extra or {}).get("metadata") or {}
        client = (
            cond.get("client")
            or (extra or {}).get("inside_agent")
            or (extra or {}).get("llm_client")
            or metadata.get("inside_agent")
            or metadata.get("llm_client")
            or metadata.get("llm_provider")
        )
        if client is not None:
            return await self._call_explicit_inside_client(
                client,
                cond,
                state,
                actor,
                candidate,
                responses,
                extra,
                entity,
            )
        if runtime_ctx is not None:
            try:
                from drama_engine.core.runtime.interactive_session.services.inside_agent import (
                    InsideAgentFactory,
                )

                client = InsideAgentFactory().get_or_create(runtime_ctx.session_metadata, cond)
            except Exception:  # noqa: BLE001 - keep evaluator fallback deterministic.
                client = None
            if client is not None:
                return await self._call_explicit_inside_client(
                    client,
                    cond,
                    state,
                    actor,
                    candidate,
                    responses,
                    extra,
                    entity,
                )
        if runtime_ctx is not None:
            actor_name = str(
                cond.get("agent_id")
                or cond.get("seat_id")
                or (extra or {}).get("inside_agent_id")
                or ""
            )
            all_names = runtime_ctx.cast.all_names()
            if not actor_name and all_names:
                actor_name = str(all_names[0])
            if actor_name in all_names:
                payload = self._build_input(
                    cond,
                    self._default_input(state, actor, candidate, responses, extra, entity),
                    state,
                    actor,
                    candidate,
                    responses,
                    extra,
                    entity,
                )
                prompt = cond.get("prompt") or json.dumps(
                    self._condition_envelope(
                        cond,
                        payload,
                        self._default_input(state, actor, candidate, responses, extra, entity),
                        actor,
                        candidate,
                        entity,
                        responses,
                        extra,
                        provider="inside",
                    ),
                    ensure_ascii=False,
                )
                value = await runtime_ctx.cast.get(actor_name).act(str(prompt), None)
                data = value.get("data") if isinstance(value, dict) else None
                if isinstance(data, dict):
                    return data
                text = str(value.get("text") or "") if isinstance(value, dict) else str(value)
                return self._decode_inside_value(text)

        return None

    async def _call_explicit_inside_client(
        self,
        client: Any,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> dict | None:
        """Call an explicitly supplied inside Agent/client."""
        payload = self._build_input(
            cond,
            self._default_input(state, actor, candidate, responses, extra, entity),
            state,
            actor,
            candidate,
            responses,
            extra,
            entity,
        )
        prompt = cond.get("prompt") or json.dumps(
            self._condition_envelope(
                cond,
                payload,
                self._default_input(state, actor, candidate, responses, extra, entity),
                actor,
                candidate,
                entity,
                responses,
                extra,
                provider="inside",
            ),
            ensure_ascii=False,
        )
        if hasattr(client, "run"):
            value = client.run(str(prompt))
        elif hasattr(client, "act"):
            value = client.act(str(prompt), None)
        elif hasattr(client, "generate_ruling"):
            value = client.generate_ruling(prompt=prompt, action=cond.get("semantic_id"), world=payload)
        elif hasattr(client, "complete"):
            value = client.complete(prompt)
        elif callable(client):
            value = client({"condition": cond, "payload": payload})
        else:
            return None
        if inspect.isawaitable(value):
            value = await value
        if isinstance(value, dict) and "text" in value and "data" in value:
            data = value.get("data")
            if isinstance(data, dict):
                return data
        return self._decode_inside_value(value)

    def _decode_inside_value(self, value: Any) -> dict:
        """Normalize an inside condition result."""
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                return {"result": value.lower() in {"1", "true", "yes", "ok"}, "text": value}
            return decoded if isinstance(decoded, dict) else {"result": bool(decoded)}
        return {"result": bool(value)}

    def _resolve_evaluator_url(self, cond: dict) -> str:
        """Resolve the HTTP endpoint URL from DSL or environment."""
        if cond.get("url"):
            return str(cond["url"])
        endpoint = str(cond.get("endpoint") or cond.get("id") or "")
        if not endpoint:
            return ""
        env_name = "DRAMA_EVALUATOR_ENDPOINT_" + "".join(
            ch if ch.isalnum() else "_"
            for ch in endpoint.upper()
        )
        return os.environ.get(env_name, "")

    def _build_input(
        self,
        cond: dict,
        default_payload: dict[str, Any],
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> Any:
        """Materialize evaluator.input include flags and `{ref: ...}` values."""
        input_spec = cond.get("input") if "input" in cond else None

        def resolve(value: Any) -> Any:
            return self._resolve_value_expr(
                value,
                state=state,
                actor=actor,
                candidate=candidate,
                responses=responses,
                extra=extra,
                entity=entity,
            )

        return self._input_builder.build(input_spec, default_payload, resolve)

    def _resolve_input_spec(
        self,
        input_spec: Any,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> Any:
        """Resolve ref expressions inside evaluator.input."""
        if isinstance(input_spec, dict):
            if set(input_spec.keys()) == {"ref"}:
                return self._resolve_value_expr(
                    input_spec,
                    state=state,
                    actor=actor,
                    candidate=candidate,
                    responses=responses,
                    extra=extra,
                    entity=entity,
                )
            return {
                key: self._resolve_input_spec(
                    value,
                    state,
                    actor,
                    candidate,
                    responses,
                    extra,
                    entity,
                )
                for key, value in input_spec.items()
            }
        if isinstance(input_spec, list):
            return [
                self._resolve_input_spec(
                    item,
                    state,
                    actor,
                    candidate,
                    responses,
                    extra,
                    entity,
                )
                for item in input_spec
            ]
        return input_spec

    def _default_input(
        self,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> dict[str, Any]:
        """Build default full evaluator input when DSL omits input."""
        extra = extra or {}
        return {
            "state": state.snapshot(),
            "players": list(state.get_attr("GAME", "players") or []),
            "participants": list(extra.get("participants") or extra.get("players") or []),
            "messages": list(extra.get("messages") or extra.get("message_history") or []),
            "actor": actor,
            "candidate": candidate,
            "entity": entity,
            "responses": responses or [],
            "current_state": extra.get("current_state"),
            "current_scene": extra.get("current_scene"),
            "patch_journal": extra.get("patch_journal") or [],
            "metadata": extra.get("metadata") or {},
        }

    def _condition_envelope(
        self,
        cond: dict,
        input_payload: dict[str, Any],
        default_input: dict[str, Any],
        actor: str | None,
        candidate: str | None,
        entity: str | None,
        responses: list | None,
        extra: dict | None,
        provider: str,
    ) -> dict[str, Any]:
        """Build a versioned envelope for external condition calls."""
        return self._protocol.build(
            runtime_type="interactive_session",
            purpose=str(cond.get("semantic_id") or cond.get("id") or "condition_evaluator"),
            provider=provider,
            input_payload=input_payload,
            context_payload={
                **default_input,
                "actor": actor,
                "candidate": candidate,
                "entity": entity,
                "responses": responses or [],
                "extra": self._serializable_extra(extra),
            },
            name=cond.get("name") or cond.get("semantic_id"),
            call_id=cond.get("id"),
            endpoint=cond.get("endpoint") or cond.get("url"),
            hook=(extra or {}).get("hook"),
            metadata={
                "current_state": (extra or {}).get("current_state"),
                "current_scene": (extra or {}).get("current_scene"),
            },
        )

    def _serializable_extra(self, extra: dict | None) -> dict[str, Any]:
        """Return extra context that can safely be encoded as JSON."""
        result: dict[str, Any] = {}
        for key, value in (extra or {}).items():
            if str(key).startswith("__"):
                continue
            if key in {"inside_agent", "llm_client", "llm_provider"}:
                continue
            try:
                json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError):
                continue
            result[str(key)] = value
        return result


__all__ = ["ExternalConditionEvaluator"]
