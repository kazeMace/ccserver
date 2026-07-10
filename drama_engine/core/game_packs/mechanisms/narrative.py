"""叙事领域机制（narrative）。

适用于文字冒险 / 分支剧情 / 剧本杀叙事：记录玩家做过的选择、走过的剧情节点、
搜集到的线索，并按累计进度选定结局。

与 cinematic 机制的区别：
- cinematic：逐条播放预制台词、点击推进（视觉小说播片语义）。
- narrative：维护「剧情状态」——分支记录、线索收集、结局判定，是分支剧情的骨架。

数据约定（与 instance._build_story_tree_panel 对齐，使其 story_tree 面板可直接投影）：
- GAME.visited_nodes  ：走过的剧情节点 id 列表。
- GAME.choice_history ：做过的选择记录 list[{node, choice}]。
- GAME.clues          ：搜集到的线索 list。
- GAME.ending         ：选定的结局 id。

机制清单：
- effect   record_choice     ：记录一次分支选择（追加到 choice_history，并把节点计入 visited_nodes）。
- effect   collect_clue      ：把一条线索加入 GAME.clues（去重）。
- effect   set_ending        ：按候选结局与阈值规则选定结局，写入 GAME.ending。
- condition narrative.clue_collected ：判断某线索是否已搜集。
- condition narrative.reached_ending ：判断是否已选定结局（或等于指定结局）。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.engine import SetAttr

logger = logging.getLogger(__name__)


def _as_list(value: Any) -> list:
    """把状态值安全转成 list 副本。"""
    return list(value) if isinstance(value, list) else []


def _handle_record_choice(effect: dict, context: Any) -> None:
    """记录一次分支选择。

    effect 字段：
      node   — 当前剧情节点 id（必填）。
      choice — 玩家所选分支 id（必填）。
    追加到 GAME.choice_history，并把 node 计入 GAME.visited_nodes（去重）。
    """
    state = context.state
    node = effect.get("node")
    choice = effect.get("choice")
    assert node and choice is not None, "record_choice 需要 node 和 choice"

    history = _as_list(state.get_attr("GAME", "choice_history"))
    history.append({"node": str(node), "choice": choice})
    context.writer.apply(SetAttr("GAME", "choice_history", history))

    visited = _as_list(state.get_attr("GAME", "visited_nodes"))
    if str(node) not in visited:
        visited.append(str(node))
        context.writer.apply(SetAttr("GAME", "visited_nodes", visited))
    logger.debug("[record_choice] node=%s choice=%s", node, choice)


def _handle_collect_clue(effect: dict, context: Any) -> None:
    """把一条线索加入 GAME.clues（去重）。

    effect 字段：
      clue — 线索 id 或对象（必填）；相同 id/值不重复加入。
      to   — 线索列表路径，默认 GAME.clues。
    """
    clue = effect.get("clue")
    assert clue is not None, "collect_clue 需要 clue"
    to_path = effect.get("to", "GAME.clues")
    entity, attr = _split_path(to_path)
    clues = _as_list(context.state.get_attr(entity, attr))
    if clue not in clues:
        clues.append(clue)
        context.writer.apply(SetAttr(entity, attr, clues))
    logger.debug("[collect_clue] clue=%s total=%d", clue, len(clues))


def _handle_set_ending(effect: dict, context: Any) -> None:
    """按候选结局与门槛规则选定结局，写入状态。

    effect 字段：
      to    — 结局写入路径，默认 GAME.ending。
      rules — 结局规则列表，按顺序匹配第一个满足者：
              [{ending: good_end, attr: GAME.affection, at_least: 5}, ...]
              规则字段 attr(状态路径) + at_least / below（数值门槛，二选一）。
      default — 无规则命中时的兜底结局（可选）。
    """
    state = context.state
    rules = effect.get("rules") or []
    chosen = None
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        attr_path = rule.get("attr")
        value = _read_path(state, attr_path) if attr_path else None
        if _rule_matches(rule, value):
            chosen = rule.get("ending")
            break
    if chosen is None:
        chosen = effect.get("default")
    if chosen is None:
        return
    to_path = effect.get("to", "GAME.ending")
    entity, attr = _split_path(to_path)
    context.writer.apply(SetAttr(entity, attr, chosen))
    logger.debug("[set_ending] ending=%s", chosen)


def _rule_matches(rule: dict, value: Any) -> bool:
    """判断一条结局规则是否命中。

    有 at_least/below 门槛时按数值比较；都没有时视为无条件命中（兜底规则）。
    """
    at_least = rule.get("at_least")
    below = rule.get("below")
    if at_least is None and below is None:
        return True
    if not isinstance(value, (int, float)):
        return False
    if isinstance(at_least, (int, float)) and value < at_least:
        return False
    if isinstance(below, (int, float)) and value >= below:
        return False
    return True


def _cond_clue_collected(spec: dict, context: dict) -> bool:
    """判断某线索是否已搜集。

    spec.input（或 spec）字段：clue（线索 id/值）、from（列表路径，默认 GAME.clues）。
    """
    state = context.get("state")
    if state is None:
        return False
    source = spec.get("input") if isinstance(spec.get("input"), dict) else spec
    clue = source.get("clue")
    from_path = source.get("from", "GAME.clues")
    if clue is None:
        return False
    clues = _as_list(_read_path(state, from_path))
    return clue in clues


def _cond_reached_ending(spec: dict, context: dict) -> bool:
    """判断是否已选定结局。

    spec.input（或 spec）字段：ending（可选，指定则要求等于它）、from（路径，默认 GAME.ending）。
    """
    state = context.get("state")
    if state is None:
        return False
    source = spec.get("input") if isinstance(spec.get("input"), dict) else spec
    from_path = source.get("from", "GAME.ending")
    current = _read_path(state, from_path)
    expected = source.get("ending")
    if expected is not None:
        return current == expected
    return current is not None


def _read_path(state: Any, path: Any) -> Any:
    """读取 "ENTITY.attr" 状态路径的值。"""
    if not isinstance(path, str) or "." not in path:
        return None
    entity, attr = path.split(".", 1)
    return state.get_attr(entity, attr)


def _split_path(path: str) -> tuple[str, str]:
    """把 "ENTITY.attr" 拆成 (entity, attr)。"""
    assert isinstance(path, str) and "." in path, f"状态路径必须是 ENTITY.attr: {path}"
    entity, attr = path.split(".", 1)
    return entity, attr


def register(api: Any) -> None:
    """把 narrative 机制注册进 PluginRegistry。"""
    api.register_effect("record_choice", _handle_record_choice)
    api.register_effect("collect_clue", _handle_collect_clue)
    api.register_effect("set_ending", _handle_set_ending)
    api.register_condition("narrative.clue_collected", _cond_clue_collected)
    api.register_condition("narrative.reached_ending", _cond_reached_ending)


def build_narrative_projection_profile() -> Any:
    """构建叙事投影档案。

    panels.story_tree 声明前端渲染剧情分支树；instance._build_story_tree_panel 的
    取数逻辑已就绪（读 GAME.visited_nodes/choice_history），这里激活它。
    """
    from drama_engine.core.interaction.profile import ProjectionProfile
    return ProjectionProfile(
        panels={
            "story_tree": {"source": "story_tree"},
        },
    )


__all__ = ["register", "build_narrative_projection_profile"]
