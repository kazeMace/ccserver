"""Runtime service calls for interactive_session.

这个模块集中处理可替换能力：通过 ExecutorRegistry 分派到 llm/plugin/http/code，
或走内置策略(builtin)。Executors 只依赖这个小接口，不直接知道具体 provider。
"""

from __future__ import annotations

import json
import logging
from difflib import SequenceMatcher
from typing import Any

from drama_engine.core.components.interaction_protocol import InteractionProtocolBuilder
from drama_engine.core.components.service_input import ServiceInputBuilder
from drama_engine.core.executor import ExecutorRequest
from drama_engine.core.executor.registry import EXECUTOR_BUILTIN, TRANSPORT_EXECUTORS
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext

logger = logging.getLogger(__name__)


class RuntimeServiceCaller:
    """通过 ExecutorRegistry 分派 runtime service 调用。

    分派逻辑:
      - executor = builtin / 省略 → 走内置策略（_call_builtin）
      - executor = llm / plugin / http / code → 走 ExecutorRegistry
    """

    def __init__(self) -> None:
        """初始化 service helpers。"""
        self._input_builder = ServiceInputBuilder()
        self._protocol = InteractionProtocolBuilder()

    def _resolve_executor(self, spec: dict[str, Any]) -> str:
        """解析 executor 类型。

        兼容旧的 evaluator/provider/type 字段。
        "inside" 映射到 "llm"（inside 是旧写法，实际就是 ccserver Agent 调 LLM）。
        """
        executor = (
            spec.get("executor")
            or spec.get("provider")
            or spec.get("evaluator")
            or spec.get("type")
        )
        if executor:
            name = str(executor)
            # inside 是 llm 的旧名称
            if name == "inside":
                return "llm"
            if name in TRANSPORT_EXECUTORS:
                return name
        if spec.get("plugin"):
            return "plugin"
        return EXECUTOR_BUILTIN

    async def _call_inside_actor(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        purpose: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """借用玩家 seat 的 actor 执行 inside service（显式指定时）。

        安全策略：
          - 无显式 agent_id/seat_id → 返回 None，交给后续 executor/builtin 处理。
          - 有显式指定 → 通过 KnowledgeFirewall 生成受限上下文投影，
            绝不把全局 state 喂给玩家 actor。

        参数:
            ctx: 运行时上下文
            spec: service 声明（需要 agent_id 或 seat_id）
            purpose: 调用目的
            payload: 运行时 payload

        返回:
            dict 结果，或 None（未指定 actor 时）
        """
        actor_name = str(
            spec.get("agent_id")
            or spec.get("seat_id")
            or ctx.session_metadata.get("inside_agent_id")
            or ""
        )
        all_names = ctx.cast.all_names()

        # 未显式指定 → 不借用任何玩家，交给后续路径
        if not actor_name or actor_name not in all_names:
            return None

        # 生成该 actor 的 firewall 受限投影（不含他人秘密）
        restricted_context = ctx.project_for_actor(actor_name, purpose="prompt")

        # 构造 prompt：优先用 spec 显式提供的，否则把 restricted_context + payload 序列化
        prompt = spec.get("prompt") or json.dumps(
            {"purpose": purpose, "context": restricted_context, "payload": payload},
            ensure_ascii=False,
        )

        # 调用该 seat 的 actor
        response = await ctx.cast.get(actor_name).act(str(prompt), None)

        # 解析响应
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

    async def call_async(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any] | None,
        purpose: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """调用一个 runtime service（async）。

        参数:
            ctx: 运行时上下文
            spec: DSL service 声明
            purpose: 调用目的（用于 builtin 分派）
            payload: 运行时 payload

        返回:
            dict 结果
        """
        service_spec = dict(spec or {})
        service_payload = self._build_service_payload(ctx, service_spec, payload)

        # 优先检查：spec 指定了借用某个玩家 seat 的 actor 执行
        actor_result = await self._call_inside_actor(ctx, service_spec, purpose, service_payload)
        if actor_result is not None:
            return actor_result

        executor_type = self._resolve_executor(service_spec)

        # builtin: 走内置确定性策略
        if executor_type == EXECUTOR_BUILTIN:
            return self._call_builtin(ctx, service_spec, purpose, service_payload)

        # 通过 ExecutorRegistry 分派到 llm/plugin/http/code
        registry = ctx.executor_registry
        if registry is None or not registry.has(executor_type):
            logger.warning(
                "[RuntimeServiceCaller] executor '%s' 不可用，fallback to builtin (purpose=%s)",
                executor_type, purpose,
            )
            return self._call_builtin(ctx, service_spec, purpose, service_payload)

        # 构造 ExecutorRequest
        request = self._build_executor_request(ctx, service_spec, purpose, service_payload, executor_type)
        response = await registry.execute(executor_type, request)

        if not response.success:
            logger.warning(
                "[RuntimeServiceCaller] executor '%s' 调用失败: %s，fallback to builtin",
                executor_type, response.error,
            )
            return self._call_builtin(ctx, service_spec, purpose, service_payload)

        return response.data

    def call_sync(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any] | None,
        purpose: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """同步调用（仅支持 builtin）。

        非 builtin 的 executor 需要 async，同步路径直接走 builtin fallback。
        """
        service_spec = dict(spec or {})
        service_payload = self._build_service_payload(ctx, service_spec, payload)
        return self._call_builtin(ctx, service_spec, purpose, service_payload)

    def _build_executor_request(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        purpose: str,
        payload: dict[str, Any],
        executor_type: str,
    ) -> ExecutorRequest:
        """构造 ExecutorRequest。"""
        config: dict[str, Any] = {}

        if executor_type == "llm":
            # prompt: 优先用 spec 中显式指定的，否则用 payload 序列化
            prompt = spec.get("prompt") or json.dumps(payload, ensure_ascii=False)
            config["model_name"] = spec.get("model") or spec.get("model_name")
            config["api_key"] = spec.get("api_key")
            config["base_url"] = spec.get("base_url")
            config["system_prompt"] = spec.get("system") or spec.get("system_prompt")
            # 清除 None 值
            config = {k: v for k, v in config.items() if v is not None}
            return ExecutorRequest(
                purpose=purpose,
                payload={"prompt": prompt},
                config=config,
            )

        elif executor_type == "plugin":
            config["name"] = spec.get("name") or spec.get("plugin") or spec.get("id") or purpose
            return ExecutorRequest(
                purpose=purpose,
                payload=payload,
                config=config,
            )

        elif executor_type == "http":
            config["url"] = spec.get("url") or spec.get("endpoint") or ""
            config["method"] = spec.get("method") or "POST"
            config["headers"] = spec.get("headers") or {}
            config["timeout_ms"] = spec.get("timeout_ms") or 10000
            return ExecutorRequest(
                purpose=purpose,
                payload=payload,
                config=config,
            )

        elif executor_type == "code":
            config["language"] = spec.get("language") or "python"
            config["code"] = spec.get("code") or ""
            config["env"] = spec.get("env") or {}
            return ExecutorRequest(
                purpose=purpose,
                payload={"state": ctx.state.snapshot() if ctx.state else {}},
                config=config,
            )

        return ExecutorRequest(purpose=purpose, payload=payload, config=config)

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

    def _call_builtin(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        purpose: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """内置确定性策略分派。"""
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
        """模糊匹配玩家文本到最接近的选项。"""
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
        """从 response data 中检测 schedule patch。"""
        response = payload.get("source_response") or {}
        data = response.get("data")
        if isinstance(data, dict) and isinstance(data.get("schedule_patch"), dict):
            return {"patch": dict(data["schedule_patch"])}
        if "patch" in spec and isinstance(spec["patch"], dict):
            return {"patch": dict(spec["patch"])}
        if not str(response.get("text") or ""):
            return {}
        return {"patch": None}

    def _choose_ending(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        """根据条件选择结局。"""
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
        """轮询确定下一个发言者。"""
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
        """确定性 fallback 故事生成。"""
        text = str(spec.get("text") or payload.get("text") or "")
        if not text:
            text = "剧情继续向前推进。"
        return {"text": text, "beats": [{"text": text}]}

    def _generate_flow_patch(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """确定性 fallback flow patch 生成。"""
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
        """返回 spec 中的 fallback 或空结果。"""
        fallback = spec.get("fallback")
        if isinstance(fallback, dict):
            return dict(fallback)
        if purpose == "story_generator":
            return self._generate_story(spec, payload)
        return {}
