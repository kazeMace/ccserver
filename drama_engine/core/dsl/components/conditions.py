# drama_engine/components/conditions.py
"""
条件原语求值器（ConditionEvaluator）。

负责把 YAML 中的 when: 字段求值为 True/False。
原语覆盖不了的情况可以用 python 执行兜底，或用 expr + default 预留 LLM 兜底。

道具数量存储约定：
  entity.inventory_<item_name> = int（数量）
  "unlimited" 字符串表示无限，item_available 始终返回 True
"""

from __future__ import annotations
import json
import logging
import os
import subprocess
import urllib.error
import urllib.request
from typing import Any

from drama_engine.core.engine import State
from drama_engine.core.dsl.components.value_resolver import ValueResolver


_NEW_OPERATOR_KEYS = {
    "equal",
    "not_equal",
    "greater_than",
    "less_than",
    "greater_than_equal",
    "less_than_equal",
    "in",
    "not_in",
    "is_null",
    "not_null",
}

_OLD_OPERATOR_KEYS = {
    "equals",
    "not_equals",
    "gte",
    "lte",
    "gt",
    "lt",
    "equals_state",
    "not_equals_state",
    "in",
    "not_in",
    "is_null",
    "not_null",
}

_CONDITION_KEYS = _NEW_OPERATOR_KEYS | _OLD_OPERATOR_KEYS | {
    "all",
    "any",
    "not",
    # Preferred unified condition syntax.
    "evaluator",
    "id",
    "ref",
    "left",
    "op",
    "right",
    "expected",
    "pass_when",
    "fallback",
    "runtime",
    "language",
    "env",
    "code",
    "timeout_ms",
    "endpoint",
    "url",
    "input",
    "output_schema",
    "min_confidence",
    # Legacy condition syntax kept for old scripts.
    "state",
    "value",
    "count",
    "item_available",
    "just_died",
    "is_first_round",
    "python",
    "expr",
    "plugin",
}


class ConditionEvaluator:
    """
    条件原语求值器。

    支持的原语：
      - 统一写法：{ref: GAME.round, op: greater_than_equal, value: 2}
      - 通用比较：{left: {count: {...}}, op: equal, right: 0}
      - 通用 evaluator：primitive / code / http / llm / plugin
      - value 比较：equal / not_equal / greater_than / less_than /
        greater_than_equal / less_than_equal / in / not_in
      - ref 引用：{value: {ref: GAME.round}, equal: 1}
      - 旧 state 比较兼容：equals / gte / equals_state 等
      - 计数：count + equals / gte / gte_than
      - 道具检查：item_available
      - 逻辑组合：all / any / not
      - 程序兜底：python（受限表达式或代码块）
      - LLM fallback 占位：expr（未接入 LLM 时返回 default，默认 False）

    actor / candidate 关键字：
      ref 路径里的 "actor" 会被替换为当前行动者名；
      "candidate" 会被替换为当前候选对象名，供 candidates.when 使用；
      filter 中的 value 简写会读取当前 entity 的属性。
      例：{"value": {"ref": "actor.role"}, "equal": "witch"} + actor="Player_2"
      → 实际查询 state.get_attr("Player_2", "role")
    """

    def __init__(self, plugin_registry: Any = None):
        """初始化条件求值器。"""
        self._plugins = plugin_registry
        self._values = ValueResolver(plugin_registry)

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
        """
        对一个条件 dict 求值，返回 True/False。

        参数：
          cond  — 条件字典，格式见类文档
          state — 当前游戏状态
          actor — 当前执行动作的实体名，可为 None
          candidate — 当前候选对象名，可为 None
        返回：
          bool — 条件是否成立
        """
        assert isinstance(cond, dict), f"条件必须是 dict，收到 {type(cond)}"

        # 逻辑组合原语：all / any / not
        if "all" in cond:
            return all(
                self.evaluate(c, state, actor, candidate, responses, extra, entity)
                for c in cond["all"]
            )
        if "any" in cond:
            return any(
                self.evaluate(c, state, actor, candidate, responses, extra, entity)
                for c in cond["any"]
            )
        if "not" in cond:
            return not self.evaluate(
                cond["not"], state, actor, candidate, responses, extra, entity
            )

        # 统一 evaluator 入口。没有显式 evaluator 时，ref/op/value 和
        # left/op/right 都是默认 primitive 写法；其他旧写法继续向下兼容。
        if "evaluator" in cond:
            return self._eval_by_evaluator(cond, state, actor, candidate, responses, extra, entity)
        if "left" in cond and "op" in cond:
            return self._eval_compare_condition(cond, state, actor, candidate, responses, extra, entity)
        if "ref" in cond and "op" in cond:
            return self._eval_ref_condition(cond, state, actor, candidate, responses, extra, entity)

        if "plugin" in cond:
            if self._plugins is None:
                raise ValueError(f"未配置插件注册表，无法求值 plugin condition: {cond}")
            name = cond["plugin"]
            return self._plugins.evaluate_condition(
                name,
                cond,
                {
                    "state": state,
                    "actor": actor,
                    "candidate": candidate,
                    "responses": responses or [],
                    "extra": extra or {},
                    "entity": entity,
                },
            )

        # 计数原语
        if "value" in cond:
            return self._eval_value_condition(
                cond=cond,
                state=state,
                actor=actor,
                candidate=candidate,
                responses=responses,
                extra=extra,
                entity=entity,
            )

        if "count" in cond:
            return self._eval_count(cond, state, actor)

        # 道具检查原语
        if "item_available" in cond:
            return self._eval_item_available(cond["item_available"], state, actor, candidate)

        # just_died 原语：检查实体是否在当前回合死亡
        if "just_died" in cond:
            entity = cond["just_died"]
            if entity == "actor":
                assert actor is not None, "just_died 含 'actor' 但未传入 actor"
                entity = actor
            elif entity == "candidate":
                assert candidate is not None, "just_died 含 'candidate' 但未传入 candidate"
                entity = candidate
            death_round = state.get_attr(entity, "death_round")
            current_round = state.get_attr("GAME", "round")
            return death_round is not None and death_round == current_round

        # is_first_round 原语：检查是否处于第一回合
        if "is_first_round" in cond:
            round_num = state.get_attr("GAME", "round") or 0
            result = round_num <= 1
            return result if cond["is_first_round"] else not result

        # 程序兜底：skill 可在生成 script 时写入一小段确定性判断。
        if "python" in cond:
            return self._eval_python(cond["python"], state, actor, candidate)

        # LLM fallback 占位：未接入模型判断时，返回显式 default，默认 False。
        if "expr" in cond:
            logging.getLogger(__name__).warning(
                "条件原语 expr 尚未接入 LLM 求值，返回 default=%s: %s",
                cond.get("default", False),
                cond["expr"],
            )
            return bool(cond.get("default", False))

        # state 字段比较原语
        if "state" in cond:
            return self._eval_state(cond, state, actor, candidate)

        raise ValueError(f"未知条件格式: {cond}")

    def _eval_by_evaluator(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """
        按统一 evaluator 分发条件求值。

        Args:
            cond: 条件字典，必须包含 evaluator。
            state: 当前世界状态。
            actor: 当前行动者，可为空。
            candidate: 当前候选对象，可为空。
            responses: 当前响应列表。
            extra: hook/runtime 注入的额外上下文。
            entity: filter 上下文中的当前实体。

        Returns:
            bool: 条件是否成立。
        """
        evaluator = str(cond.get("evaluator") or "primitive")
        if evaluator == "primitive":
            if "left" in cond and "op" in cond:
                return self._eval_compare_condition(cond, state, actor, candidate, responses, extra, entity)
            if "ref" in cond and "op" in cond:
                return self._eval_ref_condition(cond, state, actor, candidate, responses, extra, entity)
            # primitive evaluator 也允许包一层旧条件，便于迁移。
            nested = cond.get("condition") or cond.get("when")
            if isinstance(nested, dict):
                return self.evaluate(nested, state, actor, candidate, responses, extra, entity)
            raise ValueError(f"primitive evaluator 缺少 ref/op 或 condition: {cond}")

        if evaluator == "code":
            return self._eval_code_condition(cond, state, actor, candidate, responses, extra, entity)

        if evaluator in {"http", "llm"}:
            return self._eval_http_condition(cond, state, actor, candidate, responses, extra, entity)

        if evaluator == "plugin":
            plugin_name = cond.get("plugin") or cond.get("id")
            if self._plugins is None:
                raise ValueError(f"未配置插件注册表，无法求值 plugin evaluator: {cond}")
            if not plugin_name:
                raise ValueError(f"plugin evaluator 缺少 id/plugin: {cond}")
            return self._plugins.evaluate_condition(
                str(plugin_name),
                cond,
                {
                    "state": state,
                    "actor": actor,
                    "candidate": candidate,
                    "responses": responses or [],
                    "extra": extra or {},
                    "entity": entity,
                },
            )

        raise ValueError(f"未知 condition evaluator: {evaluator}")

    def _eval_ref_condition(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """
        求值统一 ref/op/value 条件。

        推荐写法：
          when:
            ref: GAME.round
            op: greater_than_equal
            value: 2
        """
        left = self._resolve_value_expr(
            {"ref": cond["ref"]},
            state=state,
            actor=actor,
            candidate=candidate,
            responses=responses,
            extra=extra,
            entity=entity,
            allow_entity_shorthand=False,
        )
        op = str(cond.get("op") or "equals")
        expected_spec = cond.get("value", cond.get("expected"))
        right = self._resolve_value_expr(
            expected_spec,
            state=state,
            actor=actor,
            candidate=candidate,
            responses=responses,
            extra=extra,
            entity=entity,
        )
        return self._compare_operator(left, op, right)

    def _eval_compare_condition(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """
        求值统一 left/op/right 条件。

        该写法复用 ref/op/value 的比较器，但 left/right 可以是任意
        value expression，例如 `{count: {filter: ...}}`。
        """
        left = self._resolve_value_expr(
            cond["left"],
            state=state,
            actor=actor,
            candidate=candidate,
            responses=responses,
            extra=extra,
            entity=entity,
        )
        op = str(cond.get("op") or "equals")
        right_spec = cond.get("right", cond.get("value", cond.get("expected")))
        right = self._resolve_value_expr(
            right_spec,
            state=state,
            actor=actor,
            candidate=candidate,
            responses=responses,
            extra=extra,
            entity=entity,
        )
        return self._compare_operator(left, op, right)

    def _compare_operator(self, left: Any, op: str, right: Any = None) -> bool:
        """统一比较操作符。"""
        normalized = {
            "equals": "equal",
            "eq": "equal",
            "not_equals": "not_equal",
            "ne": "not_equal",
            "gte": "greater_than_equal",
            "lte": "less_than_equal",
            "gt": "greater_than",
            "lt": "less_than",
        }.get(op, op)
        if normalized == "equal":
            if isinstance(right, bool) and left is None:
                left = False
            return left == right
        if normalized == "not_equal":
            return left != right
        if normalized == "greater_than":
            return left is not None and left > right
        if normalized == "less_than":
            return left is not None and left < right
        if normalized == "greater_than_equal":
            return left is not None and left >= right
        if normalized == "less_than_equal":
            return left is not None and left <= right
        if normalized == "in":
            return left in right
        if normalized == "not_in":
            return left not in right
        if normalized == "is_null":
            return (left is None) == bool(right)
        if normalized == "not_null":
            return (left is not None) == bool(True if right is None else right)
        raise ValueError(f"未知比较操作符: {op}")

    def _eval_value_condition(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """
        求值新版 value/operator 条件。

        `value` 表示被比较值；操作符右侧可以是字面量，也可以是
        `{ref: ...}`、`{count: ...}` 等值表达式。
        """
        value = self._resolve_value_expr(
            cond["value"],
            state=state,
            actor=actor,
            candidate=candidate,
            responses=responses,
            extra=extra,
            entity=entity,
            allow_entity_shorthand=entity is not None,
        )

        if "equal" in cond:
            expected = self._resolve_value_expr(
                cond["equal"], state, actor, candidate, responses, extra, entity
            )
            if isinstance(expected, bool) and value is None:
                value = False
            return value == expected
        if "not_equal" in cond:
            expected = self._resolve_value_expr(
                cond["not_equal"], state, actor, candidate, responses, extra, entity
            )
            return value != expected
        if "greater_than" in cond:
            expected = self._resolve_value_expr(
                cond["greater_than"], state, actor, candidate, responses, extra, entity
            )
            return value is not None and value > expected
        if "less_than" in cond:
            expected = self._resolve_value_expr(
                cond["less_than"], state, actor, candidate, responses, extra, entity
            )
            return value is not None and value < expected
        if "greater_than_equal" in cond:
            expected = self._resolve_value_expr(
                cond["greater_than_equal"], state, actor, candidate, responses, extra, entity
            )
            return value is not None and value >= expected
        if "less_than_equal" in cond:
            expected = self._resolve_value_expr(
                cond["less_than_equal"], state, actor, candidate, responses, extra, entity
            )
            return value is not None and value <= expected
        if "in" in cond:
            expected = self._resolve_value_expr(
                cond["in"], state, actor, candidate, responses, extra, entity
            )
            return value in expected
        if "not_in" in cond:
            expected = self._resolve_value_expr(
                cond["not_in"], state, actor, candidate, responses, extra, entity
            )
            return value not in expected
        if "is_null" in cond:
            return (value is None) == bool(cond["is_null"])
        if "not_null" in cond:
            return (value is not None) == bool(cond["not_null"])

        raise ValueError(f"value 条件缺少比较操作符: {cond}")

    def _eval_code_condition(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """
        求值统一 code evaluator。

        Python 默认复用受限内联执行；shell/node/bun 使用子进程执行。
        condition 代码只返回布尔，不直接修改 State。
        """
        runtime = str(cond.get("runtime") or cond.get("language") or "python")
        timeout = int(cond.get("timeout_ms") or 1000) / 1000
        env = {str(k): str(v) for k, v in dict(cond.get("env") or {}).items()}
        code = cond.get("code")
        if not code:
            raise ValueError(f"code evaluator 缺少 code: {cond}")
        if runtime == "python":
            return self._eval_python(
                {"code": code, "env": env},
                state=state,
                actor=actor,
                candidate=candidate,
            )
        return self._eval_subprocess_code(
            runtime=runtime,
            code=str(code),
            timeout=timeout,
            env=env,
            state=state,
            actor=actor,
            candidate=candidate,
            responses=responses,
            extra=extra,
            entity=entity,
        )

    def _eval_subprocess_code(
        self,
        runtime: str,
        code: str,
        timeout: float,
        env: dict[str, str],
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """运行 shell/node/bun 条件代码。"""
        payload = {
            "state": self._state_snapshot(state),
            "actor": actor,
            "candidate": candidate,
            "responses": responses or [],
            "extra": extra or {},
            "entity": entity,
        }
        command = self._code_command(runtime, code)
        process_env = os.environ.copy()
        process_env.update(env)
        process_env["DRAMA_CONDITION_CONTEXT"] = json.dumps(payload, ensure_ascii=False)
        completed = subprocess.run(
            command,
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=timeout,
            env=process_env,
            check=False,
        )
        if runtime == "shell":
            return completed.returncode == 0
        if completed.returncode != 0:
            raise ValueError(
                f"{runtime} condition 退出码 {completed.returncode}: {completed.stderr.strip()}"
            )
        output = completed.stdout.strip()
        if not output:
            return False
        try:
            decoded = json.loads(output)
        except json.JSONDecodeError:
            return output.lower() in {"1", "true", "yes", "ok"}
        if isinstance(decoded, dict):
            return bool(decoded.get("result"))
        return bool(decoded)

    def _code_command(self, runtime: str, code: str) -> list[str]:
        """返回 code evaluator 子进程命令。"""
        if runtime == "shell":
            return ["sh", "-c", code]
        if runtime == "node":
            return ["node", "-e", code]
        if runtime == "bun":
            return ["bun", "-e", code]
        raise ValueError(f"code evaluator 不支持 runtime: {runtime}")

    def _eval_http_condition(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None,
        extra: dict | None,
        entity: str | None,
    ) -> bool:
        """
        求值 http/llm evaluator。

        `id` 是语义化能力名；`endpoint` 可映射到环境变量
        DRAMA_EVALUATOR_ENDPOINT_<ID>，也可以直接用 `url`。
        """
        url = self._resolve_evaluator_url(cond)
        if not url:
            return bool(cond.get("fallback", False))
        payload = {
            "id": cond.get("id"),
            "endpoint": cond.get("endpoint"),
            "input": self._resolve_input_spec(cond.get("input") or {}, state, actor, candidate, responses, extra, entity),
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
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return bool(cond.get("fallback", False))

        confidence = result.get("confidence")
        min_confidence = cond.get("min_confidence")
        if min_confidence is not None and confidence is not None and float(confidence) < float(min_confidence):
            return bool(cond.get("fallback", False))

        pass_when = cond.get("pass_when")
        if isinstance(pass_when, dict):
            return self.evaluate(
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

    def _resolve_evaluator_url(self, cond: dict) -> str:
        """解析 http/llm evaluator 的 URL。"""
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
        """递归解析 evaluator.input 中的 ref 表达式。"""
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
                key: self._resolve_input_spec(value, state, actor, candidate, responses, extra, entity)
                for key, value in input_spec.items()
            }
        if isinstance(input_spec, list):
            return [
                self._resolve_input_spec(item, state, actor, candidate, responses, extra, entity)
                for item in input_spec
            ]
        return input_spec

    def _state_snapshot(self, state: State) -> dict[str, dict[str, Any]]:
        """构建只读状态快照，供外部 evaluator 使用。"""
        return {
            entity: {
                key: state.get_attr(entity, key)
                for key in getattr(state, "_attrs", {}).get(entity, {})
            }
            for entity in sorted(state.all_entities())
        }

    def _resolve_value_expr(
        self,
        expr: Any,
        state: State,
        actor: str | None,
        candidate: str | None,
        responses: list | None = None,
        extra: dict | None = None,
        entity: str | None = None,
        allow_entity_shorthand: bool = False,
    ) -> Any:
        """
        解析新版 value expression。

        在 filter 上下文中，`value: alive` 会被解释为 `entity.alive`；
        在 when 上下文中，字符串默认是字面量，读取上下文需使用 `{ref: ...}`。
        """
        context = dict(extra or {})
        if entity is not None:
            context["entity"] = entity

        if isinstance(expr, dict) and "count" in expr:
            return self._resolve_count(expr["count"], state)

        if isinstance(expr, dict) and "ref" in expr:
            return self._values.resolve(
                expr,
                state=state,
                responses=responses,
                actor=actor,
                candidate=candidate,
                extra=context,
            )

        if allow_entity_shorthand and isinstance(expr, str):
            return state.get_attr(entity, expr)

        return self._values.resolve(
            expr,
            state=state,
            responses=responses,
            actor=actor,
            candidate=candidate,
            extra=context,
        )

    def _eval_python(
        self,
        spec: Any,
        state: State,
        actor: str | None,
        candidate: str | None,
    ) -> bool:
        """
        执行受限 Python 条件。

        支持两种形式：
          python: "attr('GAME', 'round') == 1"
          python:
            code: |
              result = count({'alive': True, 'faction': 'wolf'}) > 0

        code 形式必须给 result 变量赋值。这里是剧本生成器的兜底能力，
        不是用户输入沙箱；仍应只运行可信 script。
        """
        extra_env = {}
        if isinstance(spec, str):
            expr = spec
            code = None
        elif isinstance(spec, dict):
            expr = spec.get("expr")
            code = spec.get("code")
            extra_env = dict(spec.get("env") or {})
        else:
            raise ValueError(f"python 条件必须是字符串或字典，收到 {type(spec)}")

        def attr(entity: str, key: str, default: Any = None) -> Any:
            value = state.get_attr(entity, key)
            return default if value is None else value

        def entities(filter_spec: dict | None = None) -> list[str]:
            names = [e for e in state.all_entities() if e != "GAME"]
            if filter_spec is None:
                return names
            return [
                e for e in names
                if self._entity_matches_filter(e, filter_spec, state)
            ]

        def count(filter_spec: dict | None = None) -> int:
            return len(entities(filter_spec))

        def having(**filter_spec: Any) -> list[str]:
            return entities(filter_spec)

        def related(relation: str, who: str) -> set[str]:
            return state.related(relation, who)

        def state_value(path: str, default: Any = None) -> Any:
            if "." not in path:
                return default
            entity_name, attr_name = path.split(".", 1)
            value = state.get_attr(entity_name, attr_name)
            return default if value is None else value

        def env_value(name: str, default: Any = None) -> Any:
            return extra_env.get(name, default)

        safe_builtins = {
            "all": all,
            "any": any,
            "bool": bool,
            "dict": dict,
            "float": float,
            "int": int,
            "len": len,
            "list": list,
            "max": max,
            "min": min,
            "set": set,
            "sorted": sorted,
            "str": str,
            "sum": sum,
        }
        env = {
            "actor": actor,
            "candidate": candidate,
            "attr": attr,
            "count": count,
            "entities": entities,
            "having": having,
            "related": related,
            "state": state_value,
            "env": env_value,
        }
        globals_env = {"__builtins__": safe_builtins, **env}

        if expr is not None:
            return bool(eval(expr, globals_env, env))
        if code is not None:
            exec(code, globals_env, env)
            if "result" not in env:
                raise ValueError("python.code 条件必须设置 result 变量")
            return bool(env["result"])
        raise ValueError(f"python 条件缺少 expr 或 code: {spec}")

    def _resolve_path(
        self,
        path: str,
        state: State,
        actor: str | None,
        candidate: str | None = None,
    ) -> Any:
        """
        解析 "entity.attr" 格式的路径，返回对应属性值。
        "actor" / "candidate" 关键字会被替换为当前上下文实体名。

        参数：
          path  — "entity.attr" 格式路径，entity 可为 "actor" / "candidate"
          state — 当前游戏状态
          actor — 当前执行者名，路径含 "actor" 时必须非 None
          candidate — 当前候选对象名，路径含 "candidate" 时必须非 None
        返回：
          属性值（Any）
        """
        if path == "actor":
            assert actor is not None, f"条件路径含 'actor' 但未传入 actor 参数: {path}"
            return actor
        if path == "candidate":
            assert candidate is not None, f"条件路径含 'candidate' 但未传入 candidate 参数: {path}"
            return candidate

        parts = path.split(".", 1)
        assert len(parts) == 2, f"state 路径必须是 'entity.attr' 格式，收到 '{path}'"
        entity, attr = parts
        if entity == "actor":
            assert actor is not None, f"条件路径含 'actor' 但未传入 actor 参数: {path}"
            entity = actor
        elif entity == "candidate":
            assert candidate is not None, f"条件路径含 'candidate' 但未传入 candidate 参数: {path}"
            entity = candidate
        return state.get_attr(entity, attr)

    def _eval_state(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        candidate: str | None = None,
    ) -> bool:
        """
        求值 state 字段比较类条件。

        支持操作符：
          equals, not_equals, is_null, not_null,
          gte, lte, gt, lt, in, not_in,
          equals_state, not_equals_state
        """
        value = self._resolve_path(cond["state"], state, actor, candidate)

        if "equals" in cond:
            expected = cond["equals"]
            # 当期望值是布尔类型时，把 None 视为 False（游戏状态未设置 = 假）
            # 例：GAME.saved 未设置时，"equals: False" 应返回 True
            if isinstance(expected, bool) and value is None:
                value = False
            return value == expected
        if "not_equals" in cond:
            return value != cond["not_equals"]
        if "is_null" in cond:
            # is_null: True 表示"期望为 None"；is_null: False 表示"期望不为 None"
            return (value is None) == cond["is_null"]
        if "not_null" in cond:
            # not_null: True 表示"期望不为 None"
            return (value is not None) == cond["not_null"]
        if "gte" in cond:
            return value is not None and value >= cond["gte"]
        if "lte" in cond:
            return value is not None and value <= cond["lte"]
        if "gt" in cond:
            return value is not None and value > cond["gt"]
        if "lt" in cond:
            return value is not None and value < cond["lt"]
        if "in" in cond:
            return value in cond["in"]
        if "not_in" in cond:
            return value not in cond["not_in"]
        if "equals_state" in cond:
            other = self._resolve_path(cond["equals_state"], state, actor, candidate)
            return value == other
        if "not_equals_state" in cond:
            other = self._resolve_path(cond["not_equals_state"], state, actor, candidate)
            return value != other

        raise ValueError(f"未知 state 比较操作符: {cond}")

    def _eval_count(self, cond: dict, state: State, actor: str | None) -> bool:
        """
        求值计数类条件。

        count 规格：{"filter": {attr: value, ...}}
        比较操作符：equals / gte / lte / gt / lt / gte_than（与另一个 count 比较）
        """
        count_spec = cond["count"]
        n = self._resolve_count(count_spec, state)

        if "equals" in cond:
            return n == cond["equals"]
        if "gte" in cond:
            return n >= cond["gte"]
        if "lte" in cond:
            return n <= cond["lte"]
        if "gt" in cond:
            return n > cond["gt"]
        if "lt" in cond:
            return n < cond["lt"]
        if "gte_than" in cond:
            # gte_than 的值也是一个 count 规格
            other_n = self._resolve_count(cond["gte_than"]["count"], state)
            return n >= other_n

        raise ValueError(f"count 条件缺少比较操作符: {cond}")

    def _resolve_count(self, count_spec: dict, state: State) -> int:
        """
        根据 count 规格统计满足 filter 条件的实体数量。
        GAME 实体始终排除在外。

        参数：
          count_spec — {"filter": {attr: value, ...}}
          state      — 当前游戏状态
        返回：
          满足 filter 的实体数量（int）
        """
        filter_spec = count_spec.get("filter", {})
        count = 0
        for entity in state.all_entities():
            if entity == "GAME":
                continue
            if self._entity_matches_filter(entity, filter_spec, state):
                count += 1
        return count

    def _entity_matches_filter(self, entity: str, filter_spec: dict, state: State) -> bool:
        """
        检查实体是否满足 filter_spec。

        兼容两种写法：
          1. 旧写法：{attr: expected_value, ...}
          2. 新写法：{value: attr, equal: expected_value} / all / any / not

        参数：
          entity      — 实体名
          filter_spec — filter 规格
          state       — 当前游戏状态
        返回：
          bool — 是否全部匹配
        """
        if self._looks_like_condition(filter_spec):
            return self.evaluate(
                filter_spec,
                state=state,
                actor=None,
                candidate=None,
                entity=entity,
            )

        for attr, expected in filter_spec.items():
            actual = state.get_attr(entity, attr)
            if actual != expected:
                return False
        return True

    def _looks_like_condition(self, spec: Any) -> bool:
        """判断一个 dict 是否是条件 AST，而不是旧 filter 属性字典。"""
        if not isinstance(spec, dict):
            return False
        return any(key in _CONDITION_KEYS for key in spec)

    def _eval_item_available(
        self,
        spec: dict,
        state: State,
        actor: str | None,
        candidate: str | None = None,
    ) -> bool:
        """
        检查实体是否持有指定道具（数量 > 0 或 "unlimited"）。

        道具存储约定：entity.inventory_<item_name> = int 或 "unlimited"

        参数：
          spec      — {"entity": str, "item": str}
          state     — 当前游戏状态
          actor     — 当前执行者名，entity 为 "actor" 时使用
          candidate — 当前候选对象名，entity 为 "candidate" 时使用
        返回：
          bool — 道具是否可用
        """
        entity = spec["entity"]
        if entity == "actor":
            assert actor is not None, "item_available 条件含 'actor' 但未传入 actor"
            entity = actor
        elif entity == "candidate":
            assert candidate is not None, "item_available 条件含 'candidate' 但未传入 candidate"
            entity = candidate
        item = spec["item"]
        attr_name = f"inventory_{item}"
        count = state.get_attr(entity, attr_name)
        if count is None:
            return False
        if count == "unlimited":
            return True
        return int(count) > 0

    def filter_entities(self, filter_spec: dict, state: State) -> set:
        """
        根据 filter 规格返回满足条件的实体名集合。
        供 performers 解析和 candidates 解析使用。

        参数：
          filter_spec — {attr: value, ...}
          state       — 当前游戏状态
        返回：
          set[str] — 满足条件的实体名集合
        """
        result = set()
        for entity in state.all_entities():
            if entity == "GAME":
                continue
            if self._entity_matches_filter(entity, filter_spec, state):
                result.add(entity)
        return result
