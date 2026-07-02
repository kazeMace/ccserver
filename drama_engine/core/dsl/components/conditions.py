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
import logging
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
        if isinstance(spec, str):
            expr = spec
            code = None
        elif isinstance(spec, dict):
            expr = spec.get("expr")
            code = spec.get("code")
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
