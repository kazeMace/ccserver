"""角色面板领域机制（stats）。

角色面板属性（血量/法力/等级/金币/好感度等）直接存在 <entity>.<attr> 上，属于
「开放属性存储」——DSL 通过 players.initial_attrs 或 state 块声明初始值即可，无需机制。
本机制补充的是「运行中改变属性」的能力，尤其是增量修改与阈值判定。

好感度这类可变关系属性推荐用 affinity_<other> 命名，例如 Player_1.affinity_Npc_A，
用 adjust_attr 增减，随剧情/互动动态变化。

机制清单：
- effect   adjust_attr        ：对某实体某属性做增量修改（read-modify-write），支持上下限夹取。
- condition stats.attr_at_least ：判断某属性 >= 阈值（如 hp>=1 存活、affinity>=5 好感达标）。
- condition stats.attr_below    ：判断某属性 < 阈值（如 hp<=0 死亡）。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.engine import SetAttr

logger = logging.getLogger(__name__)


def _handle_adjust_attr(effect: dict, context: Any) -> None:
    """对某实体某属性做增量修改。

    effect 字段：
      target — 目标实体，默认当前 actor。
      attr   — 属性名（如 hp、gold、level、affinity_Npc_A）。
      delta  — 增量（可正可负）。
      min    — 可选下限，结果不低于它。
      max    — 可选上限，结果不高于它。
    """
    state = context.state
    target = effect.get("target") or getattr(context, "actor", None)
    attr = effect.get("attr")
    assert target and attr, "adjust_attr 需要 target 和 attr"
    delta = effect.get("delta", 0)
    try:
        delta = float(delta) if isinstance(delta, float) else int(delta)
    except (TypeError, ValueError):
        delta = 0
    current = state.get_attr(target, attr)
    current = current if isinstance(current, (int, float)) else 0
    new_value = current + delta
    lower = effect.get("min")
    upper = effect.get("max")
    if isinstance(lower, (int, float)) and new_value < lower:
        new_value = lower
    if isinstance(upper, (int, float)) and new_value > upper:
        new_value = upper
    context.writer.apply(SetAttr(target, str(attr), new_value))
    logger.debug("[adjust_attr] %s.%s %s->%s", target, attr, current, new_value)


def _read_target_attr(spec: dict, context: dict) -> tuple[Any, Any]:
    """从 condition spec 解析 (属性当前值, 阈值)。"""
    state = context.get("state")
    source = spec.get("input") if isinstance(spec.get("input"), dict) else spec
    entity = source.get("entity") or context.get("actor")
    attr = source.get("attr")
    threshold = source.get("value")
    if state is None or not entity or attr is None:
        return None, threshold
    return state.get_attr(entity, attr), threshold


def _cond_attr_at_least(spec: dict, context: dict) -> bool:
    """判断某属性 >= 阈值。"""
    value, threshold = _read_target_attr(spec, context)
    if not isinstance(value, (int, float)) or not isinstance(threshold, (int, float)):
        return False
    return value >= threshold


def _cond_attr_below(spec: dict, context: dict) -> bool:
    """判断某属性 < 阈值。"""
    value, threshold = _read_target_attr(spec, context)
    if not isinstance(value, (int, float)) or not isinstance(threshold, (int, float)):
        return False
    return value < threshold


def register(api: Any) -> None:
    """把 stats 机制注册进 PluginRegistry。"""
    api.register_effect("adjust_attr", _handle_adjust_attr)
    api.register_condition("stats.attr_at_least", _cond_attr_at_least)
    api.register_condition("stats.attr_below", _cond_attr_below)


__all__ = ["register"]
