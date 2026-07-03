# drama_engine/components/candidates.py
"""
候选集解析器（CandidateResolver）。

负责解析 candidates: 字段，返回本幕可选目标列表。
支持三种基础来源、逐候选 when 过滤和无约束模式：
  - filter   ：集合筛选，支持旧属性字典和新版 value/operator 条件
  - static   ：固定列表，直接返回预设的候选列表
  - from_data：从上一幕响应中取值
  - from_state：从 State 路径读取列表
  - when     ：对每个候选单独求值，支持 actor / candidate 关键字
  - {}       ：无约束，返回所有玩家（非 GAME 实体）
"""

from __future__ import annotations
from drama_engine.core.engine import State
from drama_engine.core.dsl.components.conditions import ConditionEvaluator
from drama_engine.core.dsl.components.value_resolver import ValueResolver


class CandidateResolver:
    """
    解析 candidates: 字段，返回本幕可选目标列表。

    支持的模式：
      - filter: {attr: value, ...}
        根据属性过滤，返回所有满足条件的实体名（排除 GAME）
      - filter: {value: attr, equal: value}
        新版 selector 条件；value 简写读取当前被筛选实体的属性
      - static: [item1, item2, ...]
        固定列表，直接返回列表内容
      - from_data: "field_name"
        从上一幕响应的 data 字段中取值
      - from_state: "GAME.some_candidates"
        从 State 中读取列表/集合
      - when: 条件 dict 或条件列表
        对每个候选单独求值。条件中：
          actor     = 当前行动者
          candidate = 当前候选目标
      - {}
        无约束，返回所有玩家
    """

    def __init__(self, evaluator: ConditionEvaluator):
        """
        初始化候选集解析器。

        参数：
          evaluator — ConditionEvaluator 实例，用于属性过滤
        """
        self._eval = evaluator
        self._values = ValueResolver(getattr(evaluator, "_plugins", None))

    def resolve(
        self,
        spec: dict,
        state: State,
        last_responses: list,
        actor: str | None = None,
    ) -> list:
        """
        根据 spec 解析并返回候选集列表。

        参数：
          spec           — candidates 规格字典，支持 filter/static/from_data/from_state/when 键
          state          — 当前游戏状态
          last_responses — 上一幕的响应列表，for from_data 模式使用
          actor          — 当前行动者名，供 candidates.when 中的 actor 关键字使用
        返回：
          list[str] — 候选集列表（已排序）
        """
        assert isinstance(spec, dict), f"candidates 必须是 dict，收到 {type(spec)}"

        # 模式 1：static - 固定列表
        if "static" in spec:
            candidates = list(spec["static"])

        # 模式 2：from_data - 从上一幕响应中提取
        elif "from_data" in spec:
            field = spec["from_data"]
            if last_responses:
                # 从最后一个响应的 data 字段中取值
                data = last_responses[-1].get("data") or {}
                value = data.get(field)
                candidates = [value] if value else []
            else:
                candidates = []

        # 模式 3：from_state - 从 State 路径读取候选集
        elif "from_state" in spec:
            value = self._values.resolve(
                {"state": spec["from_state"]},
                state=state,
                actor=actor,
            )
            if value is None:
                candidates = []
            elif isinstance(value, (list, tuple, set)):
                candidates = list(value)
            else:
                candidates = [value]

        # 模式 4：filter - 集合筛选
        elif "filter" in spec:
            matched = self._eval.filter_entities(spec["filter"], state)
            candidates = sorted(matched)

        # 模式 4b：source/where - 新版通用 selector 写法
        elif "source" in spec or "where" in spec:
            matched = self._eval.filter_entities(
                {
                    "source": spec.get("source"),
                    "where": spec.get("where") or {},
                },
                state,
            )
            candidates = sorted(matched)

        # 模式 5：无约束 - 返回所有玩家（非 GAME）
        else:
            candidates = sorted(e for e in state.all_entities() if e != "GAME")

        if "extra" in spec:
            extra_values = spec["extra"]
            if not isinstance(extra_values, list):
                extra_values = [extra_values]
            for value_spec in extra_values:
                value = self._values.resolve(value_spec, state=state, actor=actor)
                if value is not None and value not in candidates:
                    candidates.append(value)

        return self._apply_when(candidates, spec.get("when"), state, actor, last_responses)

    def _apply_when(
        self,
        candidates: list,
        when_spec: dict | list | None,
        state: State,
        actor: str | None,
        last_responses: list,
    ) -> list:
        """
        对候选集执行逐候选条件过滤。

        参数：
          candidates — 基础候选列表
          when_spec  — 条件 dict 或条件列表；None 表示不过滤
          state      — 当前游戏状态
          actor      — 当前行动者名
        返回：
          list[str] — 过滤后的候选列表，保持原顺序
        """
        if not when_spec:
            return candidates

        if isinstance(when_spec, dict):
            conditions = [when_spec]
        elif isinstance(when_spec, list):
            conditions = when_spec
        else:
            raise ValueError(f"candidates.when 必须是 dict 或 list，收到 {type(when_spec)}")

        filtered = []
        for candidate in candidates:
            if all(
                self._eval.evaluate(
                    cond,
                    state,
                    actor=actor,
                    candidate=candidate,
                    responses=last_responses,
                )
                for cond in conditions
            ):
                filtered.append(candidate)
        return filtered
