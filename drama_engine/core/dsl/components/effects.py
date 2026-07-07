# drama_engine/components/effects.py
"""
效果执行器（EffectExecutor）。

负责把 YAML 中的 effects: 列表逐个执行，写入 State。

source / target 来源关键字解析：
  winner  — 从 extra["winner"] 取（由编译器在 on_result 时传入）
  actor   — 当前 actor 名
  data.target — responses[0]["data"]["target"]
  data.action — responses[0]["data"]["action"]
  data.vote   — responses[0]["data"]["vote"]
  @Player_1   — 字面量（@ 前缀）

道具数量存储约定（与 conditions.py 一致）：
  entity.inventory_<item_name> = int 或 "unlimited"
"""

from __future__ import annotations
import re
from typing import Any

from drama_engine.core.engine import State, StateWriter, SetAttr, Link, Unlink
from drama_engine.core.dsl.components.conditions import ConditionEvaluator
from drama_engine.core.dsl.components.value_resolver import ValueResolver
from drama_engine.core.dsl.plugins import EffectContext, RuleSetContext


class EffectExecutor:
    """
    效果执行器，顺序执行 effects 列表中的每个效果。
    每个效果都可以带 when 字段，不满足则跳过。
    """

    # 【H3 修复】内置 effect 类型集合，用于编译期静态校验。
    # 所有 _handle_* 方法对应的 effect.type 都应在此声明。
    BUILTIN_EFFECT_TYPES = frozenset({
        "rule_set_apply",
        "set_state",
        "add",
        "remove",
        "clear",
        "increment_state",
        "kill",
        "record_target",
        "record_current_deaths",
        "consume_item",
        "give_item",
        "build_speech_order",
        "set_relation",
        "clear_relation",
        "get_relations",
        "for_each",
        "pending_add",
        "pending_resolve",
        "flow_set_next",
        "summarize",
        "broadcast",
        "add_score",
        "advance_turn",
    })

    def __init__(self, evaluator: ConditionEvaluator, plugin_registry: Any = None):
        # 条件求值器，用于评估 when 字段
        self._eval = evaluator
        self._values = ValueResolver(plugin_registry)
        self._plugins = plugin_registry

    def execute_all(self, effects: list, state: State, writer: StateWriter,
                    responses: list, actor: str | None, extra: dict = None):
        """
        按顺序执行 effects 列表中的所有效果。

        参数：
          effects   — 效果字典列表
          state     — 当前游戏状态（只读查询用）
          writer    — 状态写入器（唯一写入口）
          responses — 本幕收到的所有玩家响应列表
          actor     — 当前执行动作的实体名，可为 None
          extra     — 附加上下文字典（如 winner 等）
        """
        if extra is None:
            extra = {}
        extra.setdefault("__state", state)
        for effect in effects:
            self.execute(effect, state, writer, responses, actor, extra)

    def execute(self, effect: dict, state: State, writer: StateWriter,
                responses: list, actor: str | None, extra: dict = None):
        """
        执行单个效果。

        如果效果带有 when 字段，先求值条件，不满足则跳过。

        参数：
          effect    — 效果字典，必须含 "type" 字段
          state     — 当前游戏状态
          writer    — 状态写入器
          responses — 本幕收到的所有玩家响应列表
          actor     — 当前执行动作的实体名，可为 None
          extra     — 附加上下文字典（如 winner 等）
        """
        if extra is None:
            extra = {}
        extra.setdefault("__state", state)

        if "condition" in effect:
            raise ValueError("effects[].condition 已删除，请改用 effects[].when")

        # 如果有 when 字段，先求值；不满足则跳过本效果
        if "when" in effect:
            if not self._evaluate_when(effect["when"], state, actor, responses, extra):
                return

        effect_type = effect.get("type")
        assert effect_type, f"效果缺少 type 字段: {effect}"

        if self._plugins is not None and self._plugins.has_effect(effect_type):
            context = EffectContext(
                state=state,
                writer=writer,
                actor=actor,
                responses=responses,
                scene_name=str(extra.get("scene_name", "")),
                extra=extra,
            )
            self._plugins.execute_effect(effect, context)
            return

        # 动态分发到对应 handler
        handler = getattr(self, f"_handle_{effect_type}", None)
        if handler is None:
            raise ValueError(f"未知效果类型: {effect_type}")
        handler(effect, state, writer, responses, actor, extra)

    def _evaluate_when(
        self,
        cond: dict,
        state: State,
        actor: str | None,
        responses: list,
        extra: dict,
    ) -> bool:
        """
        求值 effect.when。

        【H4 修复】复用 ConditionEvaluator 的逻辑，仅处理 effect 特有的上下文路径：
          - data.xxx：读取本幕响应数据，例如 data.action
          - winner 或 winner.xxx：读取投票胜出者或其状态属性
          - selection_result / item：其他上下文关键字

        对于非上下文路径的普通条件（all/any/not/value/state/count/...），
        直接委托给 ConditionEvaluator，避免重复实现。
        """
        # 检查是否是 effect 特有的上下文路径条件
        path = cond.get("state")
        if isinstance(path, str) and self._is_context_path(path):
            # 这是 effect 特有的上下文路径（data/winner/selection_result/item），
            # 需要特殊解析，然后手动比较。
            value = self._resolve_source(path, actor, responses, extra)
            return self._compare_value(value, cond, state, actor, responses, extra)

        # 所有其他条件（all/any/not/value/count/left/ref/plugin/...）
        # 直接委托给 ConditionEvaluator，复用已有逻辑，避免重复实现 all/any/not。
        return self._eval.evaluate(
            cond,
            state,
            actor,
            responses=responses,
            extra=extra,
        )

    def _is_context_path(self, path: str) -> bool:
        """判断 path 是否需要本幕上下文解析。"""
        prefixes = (
            "data",
            "winner",
            "selection_result",
            "item",
        )
        return path in prefixes or any(path.startswith(prefix + ".") for prefix in prefixes)

    def _compare_value(
        self,
        value: Any,
        cond: dict,
        state: State,
        actor: str | None,
        responses: list,
        extra: dict,
    ) -> bool:
        """
        对已解析出的值执行常见比较操作。

        【H4 修复】这里的比较逻辑与 primitive.py:evaluate_state_condition 平行，
        但区别在于需要通过 _resolve_source 解析 effect 特有的上下文（data/winner）。
        保留这个方法，但内部逻辑与 primitive 保持一致，避免操作符分歧。

        如果后续需要添加新操作符（如 gte/lte/gt/lt），在这里和 primitive.py 同步添加。
        """
        # 布尔值特殊处理：None 视为 False
        if "equals" in cond:
            expected = self._resolve_source(cond["equals"], actor, responses, extra)
            if isinstance(expected, bool) and value is None:
                value = False
            return value == expected
        if "not_equals" in cond:
            expected = self._resolve_source(cond["not_equals"], actor, responses, extra)
            return value != expected
        if "is_null" in cond:
            return (value is None) == cond["is_null"]
        if "not_null" in cond:
            return (value is not None) == cond["not_null"]
        if "in" in cond:
            expected = self._resolve_source(cond["in"], actor, responses, extra)
            return value in expected
        if "not_in" in cond:
            expected = self._resolve_source(cond["not_in"], actor, responses, extra)
            return value not in expected
        if "equals_state" in cond:
            # equals_state 比较另一个状态路径，使用 ValueResolver
            other = self._values.resolve(
                cond["equals_state"], state, responses, actor, None, extra
            )
            return value == other
        if "not_equals_state" in cond:
            other = self._values.resolve(
                cond["not_equals_state"], state, responses, actor, None, extra
            )
            return value != other
        # 数值比较操作符（与 primitive.py:evaluate_state_condition L165-172 对齐）
        if "gte" in cond:
            expected = self._resolve_source(cond["gte"], actor, responses, extra)
            return value is not None and value >= expected
        if "lte" in cond:
            expected = self._resolve_source(cond["lte"], actor, responses, extra)
            return value is not None and value <= expected
        if "gt" in cond:
            expected = self._resolve_source(cond["gt"], actor, responses, extra)
            return value is not None and value > expected
        if "lt" in cond:
            expected = self._resolve_source(cond["lt"], actor, responses, extra)
            return value is not None and value < expected
        raise ValueError(f"未知 data 条件比较操作符: {cond}")

    def _resolve_source(self, source: Any, actor: str | None,
                        responses: list, extra: dict) -> Any:
        """
        解析效果的 source / target / value 来源关键字。

        关键字：
          {state: GAME.xxx}  — 从 state 中读取属性值（dict 形式）
          winner             — extra["winner"]
          actor              — 当前 actor 名
          @Player1           — 字面量（去掉 @ 前缀）
          data.xxx           — responses[0]["data"]["xxx"]
          其他               — 原值返回（字面量）

        参数：
          source    — 来源字段值
          actor     — 当前 actor 名
          responses — 玩家响应列表
          extra     — 附加上下文
        返回：
          解析后的实际值（Any）
        """
        return self._values.resolve(
            source,
            state=extra.get("__state"),
            responses=responses,
            actor=actor,
            extra=extra,
        )

    def _resolve_entity(self, entity: Any, actor: str | None,
                        responses: list, extra: dict, state: State) -> str:
        """
        解析效果的 entity 字段。

        关键字：
          actor    — 当前 actor 名
          GAME     — 全局游戏状态实体
          data.xxx — responses[0]["data"]["xxx"]
          其他     — 原值返回

        参数：
          entity    — entity 字段值
          actor     — 当前 actor 名
          responses — 玩家响应列表
          extra     — 附加上下文
          state     — 当前游戏状态（保留备用）
        返回：
          实体名（str）
        """
        return self._values.resolve_entity(entity, state, responses, actor, None, extra)

    # ──────────────────────────────────────────────
    # 各类型效果 handler
    def _handle_rule_set_apply(self, effect: dict, state: State, writer: StateWriter,
                               responses: list, actor: str | None, extra: dict):
        """调用当前脚本声明的 rule_set handler。

        effect 字段：
          result_path — 可选，写入结果的 State 路径，如 GAME.last_rule_result。

        注意：该 effect 只建立通用调用链路；具体规则由 rule_set plugin 实现。
        """
        if self._plugins is None:
            raise ValueError("rule_set_apply 需要 plugin registry")

        rule_set = extra.get("script_rule_set") or {}
        if not isinstance(rule_set, dict) or not rule_set.get("plugin"):
            raise ValueError("rule_set_apply 需要脚本顶层 rule_set.plugin 声明")

        context = RuleSetContext(
            state=state,
            writer=writer,
            responses=responses,
            rule_set=rule_set,
            effect=effect,
            extra=extra,
        )
        result = self._plugins.apply_rule_set(context)
        result_path = effect.get("result_path")
        if result_path:
            target_effect = {"path": result_path}
            entity, attr = self._resolve_path_target(target_effect, state, responses, actor, extra)
            writer.apply(SetAttr(entity, attr, result))


    # 命名约定：_handle_<effect_type>
    # ──────────────────────────────────────────────

    def _handle_set_state(self, effect: dict, state: State, writer: StateWriter,
                          responses: list, actor: str | None, extra: dict):
        """
        设置实体属性为指定值。

        effect 字段：
          entity — 目标实体（支持 actor / GAME / data.xxx）
          attr   — 属性名
          value  — 新值（支持字面量 / winner / actor / data.xxx）
        """
        entity = self._resolve_entity(effect["entity"], actor, responses, extra, state)
        attr = effect["attr"]
        value = self._resolve_source(effect["value"], actor, responses, extra)
        writer.apply(SetAttr(entity, attr, value))

    def _resolve_path_target(
        self,
        effect: dict,
        state: State,
        responses: list,
        actor: str | None,
        extra: dict,
    ) -> tuple[str, str]:
        """
        解析 path 或 entity+attr 形式的目标位置。

        支持：
          path: GAME.flags
          entity: GAME
          attr: flags
        """
        if "path" in effect:
            path = effect["path"]
            assert isinstance(path, str) and "." in path, f"path 必须是 entity.attr 格式: {path}"
            entity, attr = path.split(".", 1)
            return (
                self._resolve_entity(entity, actor, responses, extra, state),
                attr,
            )
        return (
            self._resolve_entity(effect["entity"], actor, responses, extra, state),
            effect["attr"],
        )

    def _handle_add(self, effect: dict, state: State, writer: StateWriter,
                    responses: list, actor: str | None, extra: dict):
        """向状态集合追加一个值；底层用 list 保存，保持 YAML/JSON 友好。"""
        entity, attr = self._resolve_path_target(effect, state, responses, actor, extra)
        value = self._resolve_source(effect.get("value"), actor, responses, extra)
        current = state.get_attr(entity, attr) or []
        assert isinstance(current, (list, set, tuple)), f"{entity}.{attr} 必须是集合/list"
        new_values = list(current)
        if value not in new_values:
            new_values.append(value)
        writer.apply(SetAttr(entity, attr, new_values))

    def _handle_remove(self, effect: dict, state: State, writer: StateWriter,
                       responses: list, actor: str | None, extra: dict):
        """从状态集合移除一个值。"""
        entity, attr = self._resolve_path_target(effect, state, responses, actor, extra)
        value = self._resolve_source(effect.get("value"), actor, responses, extra)
        current = state.get_attr(entity, attr) or []
        assert isinstance(current, (list, set, tuple)), f"{entity}.{attr} 必须是集合/list"
        writer.apply(SetAttr(entity, attr, [item for item in current if item != value]))

    def _handle_clear(self, effect: dict, state: State, writer: StateWriter,
                      responses: list, actor: str | None, extra: dict):
        """清空状态集合。"""
        entity, attr = self._resolve_path_target(effect, state, responses, actor, extra)
        writer.apply(SetAttr(entity, attr, []))

    def _handle_increment_state(self, effect: dict, state: State, writer: StateWriter,
                                responses: list, actor: str | None, extra: dict):
        """
        对实体属性做数值累加。

        effect 字段：
          entity — 目标实体
          attr   — 数值属性名
          value  — 增量（默认 1）
        """
        entity = self._resolve_entity(effect["entity"], actor, responses, extra, state)
        attr = effect["attr"]
        delta = effect.get("value", 1)
        current = state.get_attr(entity, attr) or 0
        writer.apply(SetAttr(entity, attr, current + delta))

    def _handle_kill(self, effect: dict, state: State, writer: StateWriter,
                     responses: list, actor: str | None, extra: dict):
        """
        杀死目标实体：设置 alive=False、death_cause、death_round。

        effect 字段：
          target — 目标来源关键字（winner / actor / data.xxx 等）
          cause  — 死亡原因字符串（默认 "unknown"）
        """
        target = self._resolve_source(effect["target"], actor, responses, extra)
        if target is None:
            # 目标不存在时静默跳过，防止配置错误导致崩溃
            return
        cause = effect.get("cause", "unknown")
        current_round = state.get_attr("GAME", "round") or 0
        writer.apply(SetAttr(target, "alive", False))
        writer.apply(SetAttr(target, "death_cause", cause))
        writer.apply(SetAttr(target, "death_round", current_round))

    def _handle_record_target(self, effect: dict, state: State, writer: StateWriter,
                              responses: list, actor: str | None, extra: dict):
        """
        把来源实体名记录到 GAME 的指定属性（用于记录刀人目标、查验目标等）。

        effect 字段：
          attr   — GAME 上的目标属性名
          source — 来源关键字（winner / actor / data.xxx 等）
        """
        attr = effect["attr"]
        source = self._resolve_source(effect["source"], actor, responses, extra)
        writer.apply(SetAttr("GAME", attr, source))

    def _handle_record_current_deaths(self, effect: dict, state: State, writer: StateWriter,
                                      responses: list, actor: str | None, extra: dict):
        """
        记录当前回合已经出局的玩家列表。

        effect 字段：
          path / entity+attr — 写入位置

        可选字段：
          causes — 死亡原因白名单。配置后只记录 death_cause 在该列表中的玩家。

        用途：
          白天发言方向需要知道“昨晚是否有人死亡”。该 effect 通常放在
          夜晚死亡结算之后、猎人夜枪之前，避免把后续连锁死亡混入死讯参考点。
        """
        current_round = state.get_attr("GAME", "round") or 0
        causes = effect.get("causes")
        if causes is not None:
            assert isinstance(causes, list), "record_current_deaths.causes 必须是列表"
        deaths = [
            name for name in state.all_entities()
            if (
                name != "GAME"
                and state.get_attr(name, "death_round") == current_round
                and (
                    causes is None
                    or state.get_attr(name, "death_cause") in causes
                )
            )
        ]
        deaths.sort(key=lambda name: self._seat_sort_key(name, state))
        entity, attr = self._resolve_path_target(effect, state, responses, actor, extra)
        writer.apply(SetAttr(entity, attr, deaths))

    def _handle_consume_item(self, effect: dict, state: State, writer: StateWriter,
                             responses: list, actor: str | None, extra: dict):
        """
        消耗实体的指定道具（数量 -1，不低于 0；unlimited 时跳过）。

        effect 字段：
          entity — 持有道具的实体（支持 actor）
          item   — 道具名（对应 inventory_<item> 属性）
        """
        entity = self._resolve_entity(effect["entity"], actor, responses, extra, state)
        item = effect["item"]
        attr = f"inventory_{item}"
        current = state.get_attr(entity, attr)
        if current is None or current == "unlimited":
            # 道具不存在或无限，跳过
            return
        new_count = max(0, int(current) - 1)
        writer.apply(SetAttr(entity, attr, new_count))

    def _handle_give_item(self, effect: dict, state: State, writer: StateWriter,
                          responses: list, actor: str | None, extra: dict):
        """
        给实体增加指定道具数量。

        effect 字段：
          entity — 接收道具的实体（支持 actor）
          item   — 道具名（对应 inventory_<item> 属性）
          count  — 增加数量（默认 1）
        """
        entity = self._resolve_entity(effect["entity"], actor, responses, extra, state)
        item = effect["item"]
        count = effect.get("count", 1)
        attr = f"inventory_{item}"
        current = state.get_attr(entity, attr) or 0
        if current == "unlimited":
            # 无限道具无需增加
            return
        writer.apply(SetAttr(entity, attr, int(current) + count))

    def _handle_build_speech_order(self, effect: dict, state: State, writer: StateWriter,
                                   responses: list, actor: str | None, extra: dict):
        """
        根据座位顺序、参考点和方向生成发言顺序。

        effect 字段：
          path / entity+attr — 写入位置
          reference          — 参考点；可为玩家名、玩家列表或 state/data 路径
          fallback_reference — reference 为空时的备用参考点
          direction          — left/right/clockwise/counterclockwise，支持 data.target
          filter             — 进入发言名单的玩家过滤条件，默认 {"alive": true}

        规则：
          - reference 为列表时取第一个座位顺序最靠前的玩家。
          - direction=left/clockwise 使用正向座位顺序。
          - direction=right/counterclockwise 使用反向座位顺序。
          - 发言从参考点相邻的下一名符合 filter 的玩家开始，循环一圈。
        """
        direction = self._resolve_source(effect.get("direction", "left"), actor, responses, extra)
        reference = self._resolve_reference(effect.get("reference"), state, responses, actor, extra)
        if reference is None:
            reference = self._resolve_reference(
                effect.get("fallback_reference"), state, responses, actor, extra
            )

        all_players = [name for name in state.all_entities() if name != "GAME"]
        all_players.sort(key=lambda name: self._seat_sort_key(name, state))
        if not all_players:
            return

        if direction in ("right", "counterclockwise", "anticlockwise"):
            ordered_players = list(reversed(all_players))
        else:
            ordered_players = all_players

        filter_spec = effect.get("filter", {"alive": True})
        allowed_speakers = self._eval.filter_entities(filter_spec, state)
        speakers = [name for name in ordered_players if name in allowed_speakers]
        if not speakers:
            return

        reference_index = None
        if reference in ordered_players:
            reference_index = ordered_players.index(reference)
        if reference_index is None:
            ordered_speakers = speakers
        else:
            ordered_speakers = [
                name for offset in range(1, len(ordered_players) + 1)
                for name in [ordered_players[(reference_index + offset) % len(ordered_players)]]
                if name in speakers
            ]

        entity, attr = self._resolve_path_target(effect, state, responses, actor, extra)
        writer.apply(SetAttr(entity, attr, ordered_speakers))

    def _resolve_reference(
        self,
        source: Any,
        state: State,
        responses: list,
        actor: str | None,
        extra: dict,
    ) -> str | None:
        """解析发言参考点；列表取座位顺序最靠前的玩家。"""
        value = self._resolve_source(source, actor, responses, extra)
        if isinstance(value, (list, tuple, set)):
            values = [item for item in value if isinstance(item, str)]
            if not values:
                return None
            values.sort(key=lambda name: self._seat_sort_key(name, state))
            return values[0]
        if isinstance(value, str) and value:
            return value
        return None

    def _seat_sort_key(self, name: str, state: State) -> tuple:
        """按 seat_index 排序；缺失时使用 Player_N 的自然顺序兜底。"""
        seat_index = state.get_attr(name, "seat_index")
        if seat_index is not None:
            return (0, int(seat_index), name)
        match = re.search(r"(\d+)$", name)
        if match:
            return (1, int(match.group(1)), name)
        return (2, name)

    def _handle_set_relation(self, effect: dict, state: State, writer: StateWriter,
                             responses: list, actor: str | None, extra: dict):
        """
        建立实体关系边。

        effect 字段：
          relation      — 关系名
          source/target — 起点/终点，支持 data.xxx、actor、winner 等路径
          bidirectional — 是否同时建立反向边
        """
        relation = effect["relation"]
        source = self._resolve_entity(effect["source"], actor, responses, extra, state)
        target = self._resolve_entity(effect["target"], actor, responses, extra, state)
        writer.apply(Link(relation, source, target))
        if effect.get("bidirectional", False):
            writer.apply(Link(relation, target, source))

    def _handle_clear_relation(self, effect: dict, state: State, writer: StateWriter,
                               responses: list, actor: str | None, extra: dict):
        """
        清除关系边。

        source/target 可省略，省略表示对应维度通配。
        """
        relation = effect["relation"]
        source = None
        target = None
        if "source" in effect:
            source = self._resolve_entity(effect["source"], actor, responses, extra, state)
        if "target" in effect:
            target = self._resolve_entity(effect["target"], actor, responses, extra, state)
        writer.apply(Unlink(relation, source, target))

    def _handle_get_relations(self, effect: dict, state: State, writer: StateWriter,
                              responses: list, actor: str | None, extra: dict):
        """
        把某实体的关系目标集合写入状态。
        """
        relation = effect["relation"]
        source = self._resolve_entity(effect["source"], actor, responses, extra, state)
        targets = sorted(state.related(relation, source))
        entity, attr = self._resolve_path_target(effect, state, responses, actor, extra)
        writer.apply(SetAttr(entity, attr, targets))

    def _handle_for_each(self, effect: dict, state: State, writer: StateWriter,
                         responses: list, actor: str | None, extra: dict):
        """
        对列表/集合中的每个值执行一组子 effects。
        """
        items = self._resolve_source(effect["items"], actor, responses, extra) or []
        assert isinstance(items, (list, tuple, set)), f"for_each.items 必须解析为列表/集合，收到 {type(items)}"
        item_name = effect.get("as", "item")
        child_effects = effect.get("effects", [])
        assert isinstance(child_effects, list), "for_each.effects 必须是列表"
        for item in list(items):
            child_extra = dict(extra)
            child_extra[item_name] = item
            self.execute_all(child_effects, state, writer, responses, actor, child_extra)

    def _handle_pending_add(self, effect: dict, state: State, writer: StateWriter,
                            responses: list, actor: str | None, extra: dict):
        """
        向 GAME 上的 pending 队列追加一个待结算项目。
        """
        queue = effect.get("queue", "default")
        attr = f"__pending_{queue}"
        item = self._resolve_source(effect.get("item"), actor, responses, extra)
        current = state.get_attr("GAME", attr) or []
        assert isinstance(current, list), f"GAME.{attr} 必须是 list"
        writer.apply(SetAttr("GAME", attr, current + [item]))

    def _handle_pending_resolve(self, effect: dict, state: State, writer: StateWriter,
                                responses: list, actor: str | None, extra: dict):
        """
        结算 GAME 上的 pending 队列。

        本质是带自动清空能力的 for_each：
          - queue: deaths
          - as: item
          - effects: [...]
          - clear: true
        """
        queue = effect.get("queue", "default")
        attr = f"__pending_{queue}"
        items = list(state.get_attr("GAME", attr) or [])
        item_name = effect.get("as", "item")
        child_effects = effect.get("effects", [])
        assert isinstance(child_effects, list), "pending_resolve.effects 必须是列表"
        for item in items:
            child_extra = dict(extra)
            child_extra[item_name] = item
            self.execute_all(child_effects, state, writer, responses, actor, child_extra)
        if effect.get("clear", True):
            writer.apply(SetAttr("GAME", attr, []))

    def _handle_flow_set_next(self, effect: dict, state: State, writer: StateWriter,
                              responses: list, actor: str | None, extra: dict):
        """
        请求状态机流程在当前 scene 结束后切换到指定状态。

        effect 字段：
          state — 目标流程状态名，支持字面量 / data.xxx / winner 等来源

        说明：
          本 effect 只写入 GAME.__flow_next_state，不直接操作 Flow。
          Director 在 scene 结束后让 Flow 消费该请求，从而保持 effect 与
          流程控制职责分离。
        """
        next_state = self._resolve_source(effect["state"], actor, responses, extra)
        writer.apply(SetAttr("GAME", "__flow_next_state", next_state))

    def _handle_summarize(self, effect: dict, state: State, writer: StateWriter,
                          responses: list, actor: str | None, extra: dict):
        """
        汇总当前 scene/hook 上下文并写入状态。

        effect 字段：
          to / path       — 写入位置，格式为 ENTITY.attr
          format          — text 或 object，默认 text
          template        — 可选模板；声明后优先渲染模板文本
          include_raw     — format=object 时是否保留原始 responses
        """
        target = effect.get("to") or effect.get("path")
        assert isinstance(target, str) and "." in target, "summarize.to 必须是 ENTITY.attr 格式"
        entity, attr = target.split(".", 1)
        text = self._summary_text(effect, state, responses, actor, extra)
        if str(effect.get("format") or "text") in {"object", "dict"}:
            value = {
                "text": text,
                "scene": extra.get("scene_name", ""),
                "actors": [
                    str(response.get("actor"))
                    for response in responses
                    if isinstance(response, dict) and response.get("actor")
                ],
                "response_count": len(responses),
            }
            if effect.get("include_raw"):
                value["responses"] = list(responses)
                if isinstance(extra.get("controller_result"), dict):
                    value["controller_result"] = dict(extra["controller_result"])
        else:
            value = text
        if not state.has_entity(entity):
            state.register_entity(entity, {})
        writer.apply(SetAttr(entity, attr, value))

    def _summary_text(
        self,
        effect: dict,
        state: State,
        responses: list,
        actor: str | None,
        extra: dict,
    ) -> str:
        """Build a deterministic scene summary text."""
        if effect.get("template"):
            return self._render_template(effect.get("template"), state, actor, responses, extra)
        lines = []
        for response in responses:
            if not isinstance(response, dict):
                continue
            speaker = str(response.get("actor") or "")
            text = str(response.get("text") or "")
            if not text:
                data = response.get("data")
                text = "" if data is None else str(data)
            if speaker and text:
                lines.append(f"{speaker}: {text}")
            elif text:
                lines.append(text)
        controller_result = extra.get("controller_result")
        if isinstance(controller_result, dict) and controller_result.get("text"):
            lines.append("controller: " + str(controller_result["text"]))
        return "\n".join(lines)

    def _handle_broadcast(self, effect: dict, state: State, writer: StateWriter,
                          responses: list, actor: str | None, extra: dict):
        """
        把广播消息写入 GAME.__pending_broadcasts 队列。
        Director 在幕结束后统一投递。

        effect 字段：
          scope    — 消息可见域（如 "whisper:seer"、"public"）
          template — 消息模板文本
        """
        scope = effect.get("scope")
        message = effect.get("message") if isinstance(effect.get("message"), dict) else {}
        template = (
            effect.get("template")
            or effect.get("text")
            or message.get("template")
            or message.get("text")
            or ""
        )
        template = self._render_template(
            template,
            state=state,
            actor=actor,
            responses=responses,
            extra=extra,
        )
        # 读取已有队列（State 里可能已有其他待投递消息）
        pending = state.get_attr("GAME", "__pending_broadcasts") or []
        # 追加新消息（不修改原列表，新建 list 避免副作用）
        new_pending = list(pending) + [{"scope": scope, "template": template}]
        writer.apply(SetAttr("GAME", "__pending_broadcasts", new_pending))

    def _render_template(
        self,
        template: Any,
        state: State,
        actor: str | None,
        responses: list,
        extra: dict,
    ) -> str:
        """
        渲染 effect.template。

        支持：
          {actor}
          {winner}
          {selection_result.xxx}
          {item.xxx}
          {GAME.xxx}
          {data.target}
          {data.target.role}  # 先取 data.target 得到实体名，再读该实体属性
        """
        text = str(template or "")

        def replace(match: re.Match) -> str:
            expr = match.group(1).strip()
            value = self._resolve_template_expr(expr, state, actor, responses, extra)
            return "" if value is None else str(value)

        return re.sub(r"\{([^{}]+)\}", replace, text)

    def _resolve_template_expr(
        self,
        expr: str,
        state: State,
        actor: str | None,
        responses: list,
        extra: dict,
    ) -> Any:
        """解析 effect.template 中的单个表达式。"""
        if expr == "actor":
            return actor or (responses[0].get("actor") if responses else None)
        if expr == "winner":
            return extra.get("winner")
        if (
            expr == "selection_result"
            or expr.startswith("selection_result.")
            or expr == "item"
            or expr.startswith("item.")
        ):
            return self._values.resolve(
                expr,
                state=state,
                responses=responses,
                actor=actor,
                extra=extra,
            )

        parts = expr.split(".")
        if len(parts) >= 2 and parts[0] == "GAME":
            return state.get_attr("GAME", ".".join(parts[1:]))

        if len(parts) >= 2 and parts[0] == "data":
            if not responses:
                return None
            data = responses[0].get("data") or {}
            value = data.get(parts[1])
            if len(parts) == 2:
                return value
            if value is None:
                return None
            return state.get_attr(str(value), ".".join(parts[2:]))

        return None

    def _handle_add_score(self, effect: dict, state: State, writer: StateWriter,
                          responses: list, actor: str | None, extra: dict):
        """
        给队伍或实体增加分数。

        effect 字段：
          team  / entity — 目标实体名（team 优先）
          value          — 增加分数（默认 0）
        """
        team_or_entity = effect.get("team") or effect.get("entity") or actor
        value = effect.get("value", 0)
        current = state.get_attr(team_or_entity, "score") or 0
        writer.apply(SetAttr(team_or_entity, "score", current + value))

    def _handle_advance_turn(self, effect: dict, state: State, writer: StateWriter,
                             responses: list, actor: str | None, extra: dict):
        """
        把 is_turn=True 标记移给下一个玩家（按名字字母顺序定义循环顺序）。

        effect 字段：
          filter — 筛选参与轮转的实体，默认 {"alive": True}
          order  — "clockwise"（正序）或 "counterclockwise"（逆序），默认 clockwise
        """
        filter_spec = effect.get("filter", {"alive": True})
        order = effect.get("order", "clockwise")

        # 取满足 filter 的候选实体，按名字排序保证稳定顺序
        candidates = sorted(self._eval.filter_entities(filter_spec, state))
        if not candidates:
            return

        # 逆序模式反转候选列表
        if order == "counterclockwise":
            candidates = list(reversed(candidates))

        # 找到当前持有 is_turn=True 的实体下标
        current_idx = None
        for i, name in enumerate(candidates):
            if state.get_attr(name, "is_turn"):
                current_idx = i
                break

        if current_idx is None:
            # 没有人持有 is_turn，直接给第一个
            next_name = candidates[0]
        else:
            next_idx = (current_idx + 1) % len(candidates)
            # 清除当前持有者的 is_turn
            writer.apply(SetAttr(candidates[current_idx], "is_turn", False))
            next_name = candidates[next_idx]

        writer.apply(SetAttr(next_name, "is_turn", True))
