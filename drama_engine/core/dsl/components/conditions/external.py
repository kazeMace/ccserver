"""HTTP and LLM condition evaluator."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable

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
                "input": self._default_input(state, actor, candidate, responses, extra, entity),
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
        payload = {
            "id": cond.get("id"),
            "endpoint": cond.get("endpoint"),
            "input": self._resolve_input_spec(
                cond["input"] if "input" in cond else self._default_input(
                    state,
                    actor,
                    candidate,
                    responses,
                    extra,
                    entity,
                ),
                state,
                actor,
                candidate,
                responses,
                extra,
                entity,
            ),
            "context": {
                "actor": actor,
                "candidate": candidate,
                "entity": entity,
                "responses": responses or [],
                "extra": extra or {},
            },
        }
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
        """Call a synchronous inside LLM client when one is provided."""
        metadata = (extra or {}).get("metadata") or {}
        client = (extra or {}).get("llm_client") or metadata.get("llm_client") or metadata.get("llm_provider")
        if client is None:
            return None
        payload = self._default_input(state, actor, candidate, responses, extra, entity)
        prompt = cond.get("prompt") or json.dumps(payload, ensure_ascii=False)
        if hasattr(client, "generate_ruling"):
            value = client.generate_ruling(prompt=prompt, action=cond.get("semantic_id"), world=payload)
        elif hasattr(client, "complete"):
            value = client.complete(prompt)
        elif callable(client):
            value = client({"condition": cond, "payload": payload})
        else:
            return None
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
            "actor": actor,
            "candidate": candidate,
            "entity": entity,
            "responses": responses or [],
            "current_state": extra.get("current_state"),
            "current_scene": extra.get("current_scene"),
            "patch_journal": extra.get("patch_journal") or [],
            "metadata": extra.get("metadata") or {},
        }


__all__ = ["ExternalConditionEvaluator"]
