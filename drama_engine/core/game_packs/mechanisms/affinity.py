"""好感/关系领域机制（affinity）。

适用于综艺 AI / 恋综 / 约会类游戏：多人之间维护「谁对谁有多少好感」的关系矩阵，
按好感做配对、圈子分组、淘汰。

与 stats 机制的区别：
- stats.adjust_attr：单向、单个数值属性的增减（如 hp、gold、单条 affinity_<other>）。
- affinity：面向「多人成对关系」的整体操作——配对/互选判定/淘汰最低分，是矩阵级语义。

数据约定：
- 好感存在 <entity>.affinity_<other> 上（与 stats 一致），例如 Player_1.affinity_Player_2 = 5。
  这样 instance._extract_panels 的 affinity_matrix 面板可直接投影为前端好感矩阵。
- 配对结果存 GAME.pairs（list[[a, b]]），淘汰写 <entity>.eliminated=True。

机制清单：
- effect   set_affinity          ：设置 A 对 B 的好感为绝对值（区别于 stats 的增量 adjust_attr）。
- effect   pair_by_affinity      ：按互相好感之和从高到低贪心配对，结果写 GAME.pairs。
- effect   eliminate_lowest      ：淘汰收到总好感最低的实体（eliminated=True）。
- condition affinity.mutual_at_least ：判断 A、B 是否「互相」好感都 >= 阈值（配对/表白达标）。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.engine import SetAttr

logger = logging.getLogger(__name__)

# 好感属性前缀：<entity>.affinity_<other>。
_AFFINITY_PREFIX = "affinity_"


def _affinity(state: Any, source: str, target: str) -> float:
    """读取 source 对 target 的好感值，缺省 0。"""
    value = state.get_attr(source, f"{_AFFINITY_PREFIX}{target}")
    return float(value) if isinstance(value, (int, float)) else 0.0


def _players(state: Any) -> list[str]:
    """返回参与好感计算的实体列表（GAME.players，缺省扫描非 GAME 实体）。"""
    players = state.get_attr("GAME", "players")
    if isinstance(players, (list, tuple)) and players:
        return [str(name) for name in players]
    return [name for name in state.all_entities() if name != "GAME"]


def _handle_set_affinity(effect: dict, context: Any) -> None:
    """设置某实体对另一实体的好感为绝对值。

    effect 字段：
      source — 好感发出方，默认当前 actor。
      target — 好感对象（必填）。
      value  — 好感绝对值（必填，数值）。
    """
    source = effect.get("source") or getattr(context, "actor", None)
    target = effect.get("target")
    value = effect.get("value")
    assert source and target, "set_affinity 需要 source 和 target"
    assert isinstance(value, (int, float)), "set_affinity.value 必须是数值"
    context.writer.apply(SetAttr(str(source), f"{_AFFINITY_PREFIX}{target}", value))
    logger.debug("[set_affinity] %s->%s = %s", source, target, value)


def _handle_pair_by_affinity(effect: dict, context: Any) -> None:
    """按「互相好感之和」从高到低贪心配对，结果写入状态。

    effect 字段：
      candidates — 参与配对的实体列表，默认 GAME.players。
      to         — 结果写入路径，默认 GAME.pairs。
    配对规则：枚举所有两两组合，按 affinity(a,b)+affinity(b,a) 降序，贪心取不重复的对。
    奇数人时最后一人落单，不进入 pairs。
    """
    state = context.state
    candidates = effect.get("candidates")
    if not isinstance(candidates, (list, tuple)) or not candidates:
        candidates = _players(state)
    candidates = [str(name) for name in candidates]

    scored: list[tuple[float, str, str]] = []
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            a, b = candidates[i], candidates[j]
            mutual = _affinity(state, a, b) + _affinity(state, b, a)
            scored.append((mutual, a, b))
    scored.sort(key=lambda item: item[0], reverse=True)

    used: set[str] = set()
    pairs: list[list[str]] = []
    for _mutual, a, b in scored:
        if a in used or b in used:
            continue
        pairs.append([a, b])
        used.add(a)
        used.add(b)

    to_path = effect.get("to", "GAME.pairs")
    entity, attr = _split_path(to_path)
    context.writer.apply(SetAttr(entity, attr, pairs))
    logger.debug("[pair_by_affinity] pairs=%s", pairs)


def _handle_eliminate_lowest(effect: dict, context: Any) -> None:
    """淘汰「收到的总好感」最低的实体，标记 eliminated=True。

    effect 字段：
      candidates — 候选实体列表，默认 GAME.players 中未被淘汰者。
      to         — 记录被淘汰者名字的路径，默认 GAME.last_eliminated。
    平票时取候选顺序靠前者。无候选时不操作。
    """
    state = context.state
    candidates = effect.get("candidates")
    if not isinstance(candidates, (list, tuple)) or not candidates:
        candidates = [name for name in _players(state)
                      if state.get_attr(name, "eliminated") is not True]
    candidates = [str(name) for name in candidates]
    if not candidates:
        return

    others = _players(state)
    received = {name: sum(_affinity(state, other, name) for other in others if other != name)
                for name in candidates}
    loser = min(candidates, key=lambda name: received[name])
    context.writer.apply(SetAttr(loser, "eliminated", True))
    to_path = effect.get("to", "GAME.last_eliminated")
    entity, attr = _split_path(to_path)
    context.writer.apply(SetAttr(entity, attr, loser))
    logger.debug("[eliminate_lowest] loser=%s received=%s", loser, received)


def _cond_mutual_at_least(spec: dict, context: dict) -> bool:
    """判断 A、B 是否互相好感都 >= 阈值。

    spec.input（或 spec）字段：a、b（两实体名，a 缺省取 actor）、value（阈值）。
    """
    state = context.get("state")
    if state is None:
        return False
    source = spec.get("input") if isinstance(spec.get("input"), dict) else spec
    a = source.get("a") or context.get("actor")
    b = source.get("b")
    threshold = source.get("value")
    if not a or not b or not isinstance(threshold, (int, float)):
        return False
    return _affinity(state, str(a), str(b)) >= threshold and _affinity(state, str(b), str(a)) >= threshold


def _split_path(path: str) -> tuple[str, str]:
    """把 "ENTITY.attr" 拆成 (entity, attr)。"""
    assert isinstance(path, str) and "." in path, f"状态路径必须是 ENTITY.attr: {path}"
    entity, attr = path.split(".", 1)
    return entity, attr


def register(api: Any) -> None:
    """把 affinity 机制注册进 PluginRegistry。"""
    api.register_effect("set_affinity", _handle_set_affinity)
    api.register_effect("pair_by_affinity", _handle_pair_by_affinity)
    api.register_effect("eliminate_lowest", _handle_eliminate_lowest)
    api.register_condition("affinity.mutual_at_least", _cond_mutual_at_least)


def build_affinity_projection_profile() -> Any:
    """构建好感/综艺投影档案。

    panels.affinity 声明前端侧边栏展示好感矩阵；instance._extract_panels 的
    affinity_matrix 取数逻辑已就绪，这里激活它。
    """
    from drama_engine.core.interaction.profile import ProjectionProfile
    return ProjectionProfile(
        panels={
            "affinity": {"source": "affinity_matrix"},
        },
    )


__all__ = ["register", "build_affinity_projection_profile"]
