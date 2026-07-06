"""Runtime service calls for interactive_session.

这个模块集中处理“可替换能力”：插件、HTTP、LLM、内置策略。
Executors 只依赖这个小接口，不直接知道具体 provider。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import inspect
import asyncio
import threading
from difflib import SequenceMatcher
from typing import Any

from drama_engine.core.dsl.components.interaction_protocol import InteractionProtocolBuilder
from drama_engine.core.dsl.components.service_input import ServiceInputBuilder
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.services.inside_agent import InsideAgentFactory


class RuntimeServiceCaller:
    """Call built-in, plugin, HTTP, or LLM-like runtime services."""

    def __init__(self) -> None:
        """Initialize shared service helpers."""
        self._input_builder = ServiceInputBuilder()
        self._protocol = InteractionProtocolBuilder()
        self._inside_agents = InsideAgentFactory()

    def call_sync(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any] | None,
        purpose: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Call one service and return a dict result.

        Args:
            ctx: Runtime execution context.
            spec: Provider/service declaration from DSL.
            purpose: Semantic purpose, for built-in dispatch.
            payload: Fully materialized runtime payload.

        Returns:
            A dictionary result. Empty dict means no service result.

        Raises:
            ValueError: When the provider type is unknown or response shape is invalid.
        """
        service_spec = dict(spec or {})
        service_payload = self._build_service_payload(ctx, service_spec, payload)
        provider = self._service_provider(service_spec)
        if provider in {"builtin", "inside"}:
            if provider == "inside":
                inside_result = self._call_inside_client(ctx, service_spec, purpose, service_payload)
                if inside_result is not None:
                    return inside_result
            return self._call_builtin(ctx, service_spec, purpose, service_payload)
        if provider == "plugin":
            return self._call_plugin(ctx, service_spec, purpose, service_payload)
        if provider == "http":
            return self._call_http(ctx, service_spec, purpose, service_payload)
        if provider == "llm":
            return self._call_llm(ctx, service_spec, purpose, service_payload)
        raise ValueError(f"未知 runtime service provider: {provider}")

    async def call_async(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any] | None,
        purpose: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Call one service from an async runtime path.

        Args:
            ctx: Runtime execution context.
            spec: Provider/service declaration from DSL.
            purpose: Semantic purpose, for built-in dispatch.
            payload: Fully materialized runtime payload.

        Returns:
            A dictionary service result.
        """
        service_spec = dict(spec or {})
        service_payload = self._build_service_payload(ctx, service_spec, payload)
        provider = self._service_provider(service_spec)
        if provider == "inside" or (
            provider == "llm" and str(service_spec.get("provider") or "inside") == "inside"
        ):
            client_result = await self._call_inside_client_async(
                ctx,
                service_spec,
                purpose,
                service_payload,
                allow_create=True,
            )
            if client_result is not None:
                return client_result
            actor_result = await self._call_inside_actor(ctx, service_spec, purpose, service_payload)
            if actor_result is not None:
                return actor_result
            self._emit_inside_agent_warning(ctx, purpose)
            return self._call_builtin(ctx, service_spec, purpose, service_payload)
        if provider == "plugin":
            return await self._call_plugin_async(ctx, service_spec, purpose, service_payload)
        if provider == "http":
            return await self._call_http_async(ctx, service_spec, purpose, service_payload)
        if provider == "llm":
            return await asyncio.to_thread(self._call_llm, ctx, service_spec, purpose, service_payload)
        if provider == "builtin":
            return self._call_builtin(ctx, service_spec, purpose, service_payload)
        return self.call_sync(ctx, service_spec, purpose, service_payload)

    def _build_service_payload(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply DSL `input:` shaping to a provider payload."""
        if "input" not in spec:
            return dict(payload)

        def resolve(value: Any) -> Any:
            return ctx.value_resolver.resolve(
                value,
                state=ctx.state,
                responses=ctx.last_responses,
                extra=ctx.runtime_extra(),
            )

        built = self._input_builder.build(spec.get("input"), payload, resolve)
        if isinstance(built, dict):
            return built
        return {"value": built}

    def _has_inside_client(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
    ) -> bool:
        """Return whether an explicit inside client/Agent is available."""
        return self._explicit_inside_client(ctx, spec) is not None

    def _explicit_inside_client(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
    ) -> Any | None:
        """Return only explicitly injected Agent/client handles."""
        return (
            spec.get("client")
            or ctx.session_metadata.get("inside_agent")
            or ctx.session_metadata.get("llm_client")
            or ctx.session_metadata.get("llm_provider")
        )

    def _service_provider(self, spec: dict[str, Any]) -> str:
        """Resolve the provider family for a runtime service declaration."""
        evaluator = spec.get("provider") or spec.get("evaluator") or spec.get("type")
        if evaluator:
            return str(evaluator)
        if spec.get("provider"):
            return str(spec["provider"])
        if spec.get("plugin"):
            return "plugin"
        return "builtin"

    async def _call_plugin_async(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        purpose: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Call a plugin runtime service from an async path."""
        name = str(spec.get("name") or spec.get("plugin") or spec.get("id") or purpose)
        registry = ctx.plugin_registry
        if (
            registry is not None
            and hasattr(registry, "has_runtime_service")
            and registry.has_runtime_service(name)
        ):
            result = registry.call_runtime_service(
                name,
                self._plugin_payload(ctx, spec, purpose, payload),
            )
            if inspect.isawaitable(result):
                result = await result
            return self._ensure_dict(result, f"plugin service {name}")
        return self._call_plugin(ctx, spec, purpose, payload)

    def _call_plugin(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        purpose: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Call a plugin-like service.

        当前 PluginRegistry 的正式接口主要是 condition/effect/view。
        为了让 interactive_session 可以先统一 DSL，这里支持两类插件：
        1. registry 上未来扩展的 call_runtime_service(name, payload)。
        2. 当前内置语义插件名，作为可测试、可替换的默认实现。
        """
        name = str(spec.get("name") or spec.get("plugin") or spec.get("id") or purpose)
        registry = ctx.plugin_registry
        if (
            registry is not None
            and hasattr(registry, "has_runtime_service")
            and registry.has_runtime_service(name)
        ):
            result = registry.call_runtime_service(
                name,
                self._plugin_payload(ctx, spec, purpose, payload),
            )
            return self._ensure_dict(result, f"plugin service {name}")
        if registry is not None and hasattr(registry, "has_condition") and registry.has_condition(name):
            passed = ctx.condition_evaluator.evaluate(
                {"evaluator": "plugin", "name": name, **spec},
                ctx.state,
                actor=None,
                responses=ctx.last_responses,
                extra={**ctx.runtime_extra(), "service_payload": payload},
            )
            return {"result": passed}
        return self._call_builtin(ctx, {**spec, "name": name}, purpose, payload)

    def _call_http(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        purpose: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Call an HTTP runtime service."""
        url = self._resolve_url(spec)
        if not url:
            return self._fallback(spec, purpose, payload)
        timeout = int(spec.get("timeout_ms") or 10000) / 1000
        body = self._protocol.with_legacy_aliases(
            self._service_envelope(ctx, spec, purpose, payload, "http")
        )
        request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={**dict(spec.get("headers") or {}), "Content-Type": "application/json"},
            method=str(spec.get("method") or "POST").upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return self._ensure_dict(json.loads(response.read().decode("utf-8")), "http service")
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            if "fallback" in spec:
                return self._ensure_dict(spec.get("fallback") or {}, "http fallback")
            ctx.emit_host({
                "kind": "interactive_session_warning",
                "message": f"HTTP runtime service 调用失败: {exc}",
                "purpose": purpose,
            })
            return {}

    async def _call_http_async(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        purpose: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Call an HTTP runtime service without blocking the event loop."""
        return await asyncio.to_thread(self._call_http, ctx, spec, purpose, payload)

    def _call_llm(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        purpose: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Call an LLM provider or deterministic inside fallback."""
        provider = str(spec.get("provider") or "inside")
        if provider == "inside":
            result = self._call_inside_client(ctx, spec, purpose, payload)
            if result is not None:
                return result
            return self._call_builtin(ctx, spec, purpose, payload)
        return self._call_http(ctx, spec, purpose, payload)

    def _call_builtin(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        purpose: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Built-in deterministic service implementations."""
        name = str(spec.get("name") or spec.get("id") or purpose)
        if "patch" in spec and isinstance(spec["patch"], dict):
            return {"patch": dict(spec["patch"])}
        if name in {"map_free_text_to_choice", "choose_mapping"} or purpose == "choose_mapping":
            return self._map_choice(payload)
        if name in {"detect_schedule_request", "schedule_detector"} or purpose == "schedule_detector":
            return self._detect_schedule_patch(spec, payload)
        if name in {"choose_ending_by_progress", "ending_selector"} or purpose == "ending_selector":
            return self._choose_ending(ctx, spec)
        if name in {"openchat_planner", "plan_openchat_next"} or purpose == "openchat_planner":
            return self._plan_openchat_next(payload)
        if purpose in {"story_generator", "branch_generator"}:
            return self._generate_story(spec, payload)
        if purpose == "flow_patch_generator":
            return self._generate_flow_patch(ctx, spec, payload)
        return self._fallback(spec, purpose, payload)

    def _map_choice(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Map free text to the closest available choice."""
        text = str(payload.get("text") or "").lower()
        choices = list(payload.get("choices") or [])
        best_choice = choices[0] if choices else {}
        best_score = -1.0
        for choice in choices:
            choice_id = str(choice.get("id") or "")
            choice_text = str(choice.get("text") or "")
            haystack = (choice_id + " " + choice_text).lower()
            score = SequenceMatcher(None, text, haystack).ratio()
            if text and (choice_id.lower() in text or choice_text.lower() in text):
                score += 1.0
            if score > best_score:
                best_score = score
                best_choice = choice
        return {
            "selected_choice": best_choice.get("id"),
            "to": best_choice.get("to"),
            "confidence": max(0.0, best_score),
        }

    def _detect_schedule_patch(
        self,
        spec: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Detect schedule patch from spec or source response data."""
        response = payload.get("source_response") or {}
        data = response.get("data")
        if isinstance(data, dict) and isinstance(data.get("schedule_patch"), dict):
            return {"patch": dict(data["schedule_patch"])}
        text = str(response.get("text") or "")
        if "patch" in spec and isinstance(spec["patch"], dict):
            return {"patch": dict(spec["patch"])}
        if not text:
            return {}
        return {"patch": None}

    def _choose_ending(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        """Choose an ending from progress conditions."""
        endings = list(spec.get("endings") or [])
        for ending in endings:
            when = ending.get("when") if isinstance(ending, dict) else None
            if not isinstance(when, dict):
                continue
            if ctx.condition_evaluator.evaluate(
                when,
                ctx.state,
                actor=None,
                responses=ctx.last_responses,
                extra=ctx.runtime_extra(),
            ):
                return {"ending": ending.get("id") or ending.get("name")}
        if endings:
            first = endings[0]
            if isinstance(first, dict):
                return {"ending": first.get("id") or first.get("name")}
            return {"ending": str(first)}
        return {"ending": spec.get("ending")}

    def _plan_openchat_next(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Choose the next openchat speaker deterministically."""
        participants = [str(item) for item in payload.get("participants", []) or []]
        if not participants:
            return {"stop": True}
        last_response = payload.get("last_response") or {}
        last_actor = str(last_response.get("actor") or "")
        if last_actor in participants:
            index = participants.index(last_actor)
            return {"next_speaker": participants[(index + 1) % len(participants)]}
        return {"next_speaker": participants[0]}

    def _generate_story(
        self,
        spec: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate one story beat or temporary branch with deterministic fallback."""
        text = str(spec.get("text") or payload.get("text") or "")
        if not text:
            text = "剧情继续向前推进。"
        return {
            "text": text,
            "beats": [{"text": text}],
        }

    def _generate_flow_patch(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate a flow patch with deterministic fallback."""
        if isinstance(spec.get("patch"), dict):
            return {"patch": dict(spec["patch"])}
        scene_id = f"generated_{len(ctx.patch_journal.by_type('flow_patch')) + 1}"
        text = str(payload.get("text") or "新的剧情节点被创建。")
        return {
            "patch": {
                "type": "add_scene",
                "after": ctx.current_scene_id,
                "scene": {
                    "id": scene_id,
                    "type": "scene",
                    "scope": {"id": "story", "visibility": "public"},
                    "participants": {"static": []},
                    "schedule": {"mode": "none"},
                    "participant_action": {"kind": "none", "response": {"mode": "none"}},
                    "controller_action": {"enabled": False, "kind": "none"},
                    "publication": {
                        "messages": [
                            {
                                "audience": {"scope": "story"},
                                "content": {"text": text},
                            }
                        ]
                    },
                },
            }
        }

    def _fallback(
        self,
        spec: dict[str, Any],
        purpose: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Return explicit fallback or empty result."""
        fallback = spec.get("fallback")
        if isinstance(fallback, dict):
            return dict(fallback)
        if purpose == "story_generator":
            return self._generate_story(spec, payload)
        return {}

    def _call_client(
        self,
        client: Any,
        spec: dict[str, Any],
        purpose: str,
        payload: dict[str, Any],
    ) -> Any:
        """Call a user-provided LLM client."""
        prompt = spec.get("prompt") or json.dumps(payload, ensure_ascii=False)
        if hasattr(client, "run"):
            value = client.run(str(prompt))
        elif hasattr(client, "act"):
            value = client.act(str(prompt), None)
        elif hasattr(client, "generate_ruling"):
            value = client.generate_ruling(prompt=prompt, action=purpose, world=payload)
        elif hasattr(client, "complete"):
            value = client.complete(prompt)
        elif callable(client):
            value = client({"purpose": purpose, "prompt": prompt, "payload": payload})
        else:
            raise TypeError("llm_client 必须实现 run、act、generate_ruling、complete 或 callable")
        if inspect.isawaitable(value):
            return self._await_sync(value)
        return value

    def _await_sync(self, value: Any) -> Any:
        """Wait for an awaitable from a sync compatibility path."""
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

    def _call_inside_client(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        purpose: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Call a synchronous injected ccserver/LLM-like inside client."""
        client = (
            spec.get("client")
            or ctx.session_metadata.get("inside_agent")
            or ctx.session_metadata.get("llm_client")
            or ctx.session_metadata.get("llm_provider")
        )
        if client is None:
            return None
        result = self._call_client(client, spec, purpose, payload)
        return self._ensure_dict(result, "inside runtime service")

    async def _call_inside_client_async(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        purpose: str,
        payload: dict[str, Any],
        allow_create: bool = False,
    ) -> dict[str, Any] | None:
        """Call an injected ccserver Agent/client from an async runtime path."""
        client = (
            spec.get("client")
            or ctx.session_metadata.get("inside_agent")
            or ctx.session_metadata.get("llm_client")
            or ctx.session_metadata.get("llm_provider")
        )
        if client is None and allow_create:
            client = self._inside_agents.get_or_create(ctx.session_metadata, spec)
        if client is None:
            return None
        prompt = spec.get("prompt") or json.dumps(
            self._service_envelope(ctx, spec, purpose, payload, "inside"),
            ensure_ascii=False,
        )
        if hasattr(client, "run"):
            value = client.run(str(prompt))
        elif hasattr(client, "act"):
            value = client.act(str(prompt), None)
        elif hasattr(client, "generate_ruling"):
            value = client.generate_ruling(prompt=prompt, action=purpose, world=payload)
        elif hasattr(client, "complete"):
            value = client.complete(prompt)
        elif callable(client):
            value = client({"purpose": purpose, "prompt": prompt, "payload": payload})
        else:
            return None
        if inspect.isawaitable(value):
            value = await value
        if isinstance(value, dict) and "text" in value and "data" in value:
            data = value.get("data")
            if isinstance(data, dict):
                return data
        return self._ensure_dict(value, "inside runtime service")

    async def _call_inside_actor(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        purpose: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Call a live ccserver actor as the default inside agent."""
        actor_name = str(
            spec.get("agent_id")
            or spec.get("seat_id")
            or ctx.session_metadata.get("inside_agent_id")
            or ""
        )
        all_names = ctx.cast.all_names()
        if not actor_name and all_names:
            actor_name = str(all_names[0])
        if not actor_name or actor_name not in all_names:
            return None
        prompt = spec.get("prompt") or json.dumps(
            self._service_envelope(ctx, spec, purpose, payload, "inside"),
            ensure_ascii=False,
        )
        response = await ctx.cast.get(actor_name).act(str(prompt), None)
        text = str(response.get("text") or "")
        data = response.get("data")
        if isinstance(data, dict):
            return data
        if text:
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                return {"text": text, "actor": actor_name, "purpose": purpose}
            if isinstance(decoded, dict):
                return decoded
        return {"text": text, "actor": actor_name, "purpose": purpose}

    def _emit_inside_agent_warning(
        self,
        ctx: InteractiveExecutionContext,
        purpose: str,
    ) -> None:
        """Expose inside-agent creation failure once per purpose."""
        error = ctx.session_metadata.get("__interactive_inside_agent_error")
        if not error:
            return
        key = f"__interactive_inside_agent_warning_{purpose}"
        if ctx.session_metadata.get(key):
            return
        ctx.session_metadata[key] = True
        ctx.emit_host({
            "kind": "interactive_session_warning",
            "message": f"内部 Agent 初始化失败，已使用 builtin fallback: {error}",
            "purpose": purpose,
        })

    def _resolve_url(self, spec: dict[str, Any]) -> str:
        """Resolve URL from DSL or environment."""
        if spec.get("url"):
            return str(spec["url"])
        endpoint = str(spec.get("endpoint") or spec.get("id") or spec.get("name") or "")
        if not endpoint:
            return ""
        env_name = "DRAMA_RUNTIME_SERVICE_" + "".join(
            ch if ch.isalnum() else "_"
            for ch in endpoint.upper()
        )
        return os.environ.get(env_name, "")

    def _service_envelope(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        purpose: str,
        payload: dict[str, Any],
        provider: str,
    ) -> dict[str, Any]:
        """Build the unified runtime-service protocol envelope."""
        return self._protocol.build(
            runtime_type="interactive_session",
            purpose=purpose,
            provider=provider,
            input_payload=payload,
            context_payload=ctx.full_context_payload(),
            name=spec.get("name") or spec.get("plugin") or spec.get("id"),
            call_id=spec.get("id"),
            endpoint=spec.get("endpoint") or spec.get("url"),
            metadata={
                "current_state": ctx.current_state_id,
                "current_scene": ctx.current_scene_id,
            },
        )

    def _plugin_payload(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        purpose: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Return legacy plugin payload or an opt-in protocol envelope."""
        if spec.get("envelope") is True or spec.get("protocol") == "envelope":
            return self._service_envelope(ctx, spec, purpose, payload, "plugin")
        return payload

    def _ensure_dict(self, result: Any, label: str) -> dict[str, Any]:
        """Normalize a service result to dict."""
        if result is None:
            return {}
        if isinstance(result, dict):
            return result
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError:
                return {"text": result}
            if isinstance(parsed, dict):
                return parsed
        raise ValueError(f"{label} 必须返回 dict 或 JSON object 字符串")
