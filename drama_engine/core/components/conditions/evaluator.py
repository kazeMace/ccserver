"""条件求值器 — 功能层组件。

ConditionEvaluator 是 DSL `when` 条件的统一入口：
  - builtin 条件（left/op/right 等）内部处理，不走 executor
  - code/llm/http/plugin 统一通过 ExecutorRegistry 传输
  - 支持 all/any/not 逻辑组合

设计原则：
  - ConditionEvaluator 是功能组件，不是传输层
  - 传输层统一由 ExecutorRegistry 提供（llm/http/code/plugin）
  - PrimitiveConditionEvaluator 保持不变（纯内存比较，无需 executor）
"""

from __future__ import annotations

import json
import logging
from typing import Any

from drama_engine.core.components.conditions.primitive import PrimitiveConditionEvaluator
from drama_engine.core.engine import State
from drama_engine.core.executor.base import ExecutorRequest

logger = logging.getLogger(__name__)

# 条件 dict 中属于控制字段（不作为 executor config 传递）
_CONTROL_KEYS = frozenset({
    "executor", "all", "any", "not",
    "left", "op", "right", "ref", "value",
    "condition", "when", "plugin", "name", "id",
    "pass_when", "fallback", "min_confidence",
    "input", "semantic_id", "prompt",
    "code", "language", "env",
    "python", "expr", "state", "count",
    "item_available", "default",
    "_guard_text",
})


class ConditionEvaluator:
    """条件求值器 — DSL `when` 的统一入口。

    构造参数：
      executor_registry — ExecutorRegistry 实例（可选；无时只支持 builtin 条件）
      plugin_registry   — 插件注册表（传给 PrimitiveConditionEvaluator 做 filter）
    """

    def __init__(
        self,
        plugin_registry: Any = None,
        executor_registry: Any = None,
    ) -> None:
        """初始化条件求值器。

        参数：
            plugin_registry: 插件注册表（供 primitive filter 和 plugin executor 使用）
            executor_registry: ExecutorRegistry 实例（可选，无时只支持 builtin）
        """
        self._executor_registry = executor_registry
        self._plugins = plugin_registry
        self._primitive = PrimitiveConditionEvaluator(
            plugin_registry=plugin_registry,
            evaluate_condition=self.evaluate,
        )

    def evaluate(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None = None,
        responses: list | None = None,
        extra: dict | None = None,
        entity: str | None = None,
    ) -> bool:
        """同步求值（仅支持 builtin 条件和逻辑组合器）。

        对于需要 executor 的条件（llm/http/code/plugin），同步路径返回 fallback。
        完整功能请使用 evaluate_async。
        """
        assert isinstance(cond, dict), f"条件必须是 dict，收到 {type(cond)}"

        # 逻辑组合器
        if "all" in cond:
            return all(
                self.evaluate(item, state, actor, candidate, responses, extra, entity)
                for item in cond["all"]
            )
        if "any" in cond:
            return any(
                self.evaluate(item, state, actor, candidate, responses, extra, entity)
                for item in cond["any"]
            )
        if "not" in cond:
            return not self.evaluate(cond["not"], state, actor, candidate, responses, extra, entity)

        # 显式 executor
        executor = cond.get("executor")
        if executor and executor not in {"builtin", "primitive"}:
            # code executor 可以同步执行（纯本地操作）
            if executor == "code":
                return self._evaluate_code_sync(cond, state, actor, candidate, responses, extra, entity)
            # 其他 executor（llm/http/plugin）同步路径无法调用，返回 fallback
            logger.debug("[ConditionEvaluator] 同步路径不支持 executor=%s，返回 fallback", executor)
            return bool(cond.get("fallback", False))

        # builtin 条件
        return self._evaluate_builtin(cond, state, actor, candidate, responses, extra, entity)

    async def evaluate_async(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None = None,
        responses: list | None = None,
        extra: dict | None = None,
        entity: str | None = None,
    ) -> bool:
        """异步求值（主入口，支持所有 executor 类型）。"""
        assert isinstance(cond, dict), f"条件必须是 dict，收到 {type(cond)}"

        # 逻辑组合器（短路求值）
        if "all" in cond:
            for item in cond["all"]:
                if not await self.evaluate_async(item, state, actor, candidate, responses, extra, entity):
                    return False
            return True
        if "any" in cond:
            for item in cond["any"]:
                if await self.evaluate_async(item, state, actor, candidate, responses, extra, entity):
                    return True
            return False
        if "not" in cond:
            return not await self.evaluate_async(cond["not"], state, actor, candidate, responses, extra, entity)

        # 显式 executor
        executor = cond.get("executor")
        if executor and executor not in {"builtin", "primitive"}:
            return await self._evaluate_via_executor(executor, cond, state, actor, candidate, responses, extra, entity)

        # 隐式 plugin（无 executor 字段但有 plugin key）
        if "plugin" in cond and "executor" not in cond:
            return await self._evaluate_via_executor("plugin", cond, state, actor, candidate, responses, extra, entity)

        # builtin 条件（纯内存，同步即可）
        return self._evaluate_builtin(cond, state, actor, candidate, responses, extra, entity)

    # ──────────────────────────────────────────────
    # builtin 路径（PrimitiveConditionEvaluator）
    # ──────────────────────────────────────────────

    def _evaluate_builtin(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """内置 builtin 条件求值。"""
        # executor: builtin 可能包含嵌套 condition/when
        if cond.get("executor") in {"builtin", "primitive"}:
            nested = cond.get("condition") or cond.get("when")
            if isinstance(nested, dict):
                return self.evaluate(nested, state, actor, candidate, responses, extra, entity)
            # 没有嵌套，继续尝试 left/op 等

        if "left" in cond and "op" in cond:
            return self._primitive.evaluate_compare_condition(
                cond, state, actor, candidate, responses, extra, entity,
            )
        if "ref" in cond and "op" in cond:
            return self._primitive.evaluate_ref_condition(
                cond, state, actor, candidate, responses, extra, entity,
            )
        if "value" in cond:
            return self._primitive.evaluate_value_condition(
                cond=cond, state=state, actor=actor,
                candidate=candidate, responses=responses, extra=extra, entity=entity,
            )
        if "count" in cond:
            return self._primitive.evaluate_count_condition(cond, state)
        if "item_available" in cond:
            return self._primitive.evaluate_item_available(cond["item_available"], state, actor, candidate)
        if "state" in cond:
            return self._primitive.evaluate_state_condition(cond, state, actor, candidate)
        raise ValueError(f"未知条件格式: {cond}")

    def _evaluate_code_sync(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """同步执行 executor: code 条件（本地 python/shell/subprocess）。"""
        from drama_engine.core.components.conditions.code import CodeConditionEvaluator
        code_eval = CodeConditionEvaluator(self._primitive.entity_matches_filter)
        return code_eval.evaluate(cond, state, actor, candidate, responses, extra, entity)

    # ──────────────────────────────────────────────
    # executor 路径（统一走 ExecutorRegistry）
    # ──────────────────────────────────────────────

    async def _evaluate_via_executor(
        self,
        executor_name: str,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """通过 ExecutorRegistry 求值。"""
        if self._executor_registry is None:
            logger.warning("[ConditionEvaluator] 无 executor_registry，executor=%s 返回 fallback", executor_name)
            return bool(cond.get("fallback", False))

        request = self._build_request(executor_name, cond, state, actor, candidate, responses, extra, entity)
        try:
            response = await self._executor_registry.execute(executor_name, request)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ConditionEvaluator] executor=%s 调用失败: %s，返回 fallback", executor_name, exc)
            return bool(cond.get("fallback", False))

        return self._interpret_response(cond, response, state, actor, candidate, responses, extra, entity)

    def _build_request(
        self,
        executor_name: str,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> ExecutorRequest:
        """构造 ExecutorRequest。"""
        # payload 包含条件求值所需的上下文
        payload: dict[str, Any] = {}

        # LLM: 需要 prompt
        if executor_name == "llm":
            payload["prompt"] = cond.get("prompt") or self._build_llm_prompt(cond, state, actor, extra)

        # HTTP: payload 是完整的请求体
        if executor_name == "http":
            payload = self._build_http_payload(cond, state, actor, candidate, responses, extra, entity)

        # Code: state 和 env 传入
        if executor_name == "code":
            payload["state"] = state.snapshot() if hasattr(state, "snapshot") else {}
            payload["actor"] = actor
            payload["candidate"] = candidate

        # Plugin: 完整条件 + 上下文
        if executor_name == "plugin":
            payload["cond"] = cond
            payload["state"] = state.snapshot() if hasattr(state, "snapshot") else {}
            payload["actor"] = actor
            payload["candidate"] = candidate
            payload["responses"] = responses
            payload["extra"] = extra

        # config 是 executor 级配置（排除控制字段）
        config: dict[str, Any] = {k: v for k, v in cond.items() if k not in _CONTROL_KEYS}
        # plugin 需要 name
        if executor_name == "plugin":
            config["name"] = cond.get("plugin") or cond.get("name") or cond.get("id") or ""

        return ExecutorRequest(
            purpose="condition",
            payload=payload,
            config=config,
            context=dict(extra or {}) if extra else None,
        )

    def _build_llm_prompt(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        extra: dict | None,
    ) -> str:
        """为 LLM executor 构造默认 prompt（当 cond 没有显式 prompt 时）。"""
        semantic_id = cond.get("semantic_id") or "condition_check"
        state_snapshot = state.snapshot() if hasattr(state, "snapshot") else {}
        return (
            f"判断条件 [{semantic_id}] 是否成立。\n"
            f"当前状态: {json.dumps(state_snapshot, ensure_ascii=False, default=str)}\n"
            f"当前 actor: {actor}\n"
            "请返回 JSON: {\"result\": true/false, \"confidence\": 0~1}"
        )

    def _build_http_payload(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> dict[str, Any]:
        """为 HTTP executor 构造请求 payload。"""
        payload: dict[str, Any] = {
            "condition": {k: v for k, v in cond.items() if k not in _CONTROL_KEYS},
            "actor": actor,
            "candidate": candidate,
            "entity": entity,
        }
        # 根据 input include flags 决定包含哪些上下文
        input_spec = cond.get("input") or {}
        if input_spec.get("include_state", True):
            payload["state"] = state.snapshot() if hasattr(state, "snapshot") else {}
        if input_spec.get("include_responses") and responses:
            payload["responses"] = responses
        return payload

    def _interpret_response(
        self,
        cond: dict,
        response: Any,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """解析 ExecutorResponse 为 bool。

        逻辑：
        1. response.data["result"] 直接取 bool
        2. 如有 pass_when → 递归求值
        3. confidence < min_confidence → fallback
        4. 失败 → cond.get("fallback", False)
        """
        if response is None or not getattr(response, "success", False):
            return bool(cond.get("fallback", False))

        data = getattr(response, "data", None) or {}
        if not isinstance(data, dict):
            return bool(cond.get("fallback", False))

        # confidence 门限
        min_confidence = float(cond.get("min_confidence") or 0)
        if min_confidence > 0:
            confidence = float(data.get("confidence") or 1.0)
            if confidence < min_confidence:
                return bool(cond.get("fallback", False))

        # pass_when 二次求值
        pass_when = cond.get("pass_when")
        if isinstance(pass_when, dict):
            return self.evaluate(
                pass_when, state, actor, candidate, responses,
                {**(extra or {}), "result": data}, entity,
            )

        # 直接取 result 字段
        result = data.get("result")
        if result is None:
            result = data.get("passed") or data.get("ended")
        if result is None:
            return bool(cond.get("fallback", False))
        if isinstance(result, bool):
            return result
        if isinstance(result, str):
            return result.lower() in ("true", "1", "yes")
        return bool(result)

    # ──────────────────────────────────────────────
    # 公共工具方法
    # ──────────────────────────────────────────────

    def filter_entities(self, filter_spec: dict, state: State) -> set:
        """实体过滤。"""
        return self._primitive.filter_entities(filter_spec, state)


__all__ = ["ConditionEvaluator"]
