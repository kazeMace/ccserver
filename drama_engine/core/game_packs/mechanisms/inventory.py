"""背包/道具领域机制（inventory）。

支持两种物品形态，可同时使用：
1. 计数型：物品只有数量，存 <entity>.inventory_<item> = 数量（int 或 "unlimited"）。
   适合消耗品、UNO 计数、狼人杀药剂等。
2. 富属性型：物品带自身属性（攻击力/耐久/效果），存 <entity>.items = {item: {attrs}}。
   适合 RPG 装备、卡牌带效果。

两种形态都支持游戏过程中动态扩展：随时 grant 新物品、增减数量、转移给他人。

机制清单：
- effect  grant_item     ：给某实体增加物品（计数型加数量 / 富属性型写入 items）。
- effect  use_item       ：消耗某实体的一个计数型物品（数量减 1，unlimited 不减）。
- effect  transfer_item  ：把计数型物品从一方转移给另一方（掉落/交易）。
- condition inventory.has_item ：判断某实体是否拥有某物品（数量>0 或存在于 items）。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.engine import SetAttr

logger = logging.getLogger(__name__)

_COUNT_PREFIX = "inventory_"
_UNLIMITED = "unlimited"


def _count_of(state: Any, entity: str, item: str) -> Any:
    """读取计数型物品数量；不存在返回 0。"""
    value = state.get_attr(entity, f"{_COUNT_PREFIX}{item}")
    return value if value is not None else 0


def _handle_grant_item(effect: dict, context: Any) -> None:
    """给某实体增加物品。

    effect 字段：
      target — 获得者，默认当前 actor。
      item   — 物品名。
      count  — 计数型增加的数量（默认 1）；也可为 "unlimited"。
      attrs  — 富属性型物品的属性 dict；给出时写入 <target>.items[item]。
    """
    state = context.state
    target = effect.get("target") or getattr(context, "actor", None)
    item = effect.get("item")
    assert target and item, "grant_item 需要 target 和 item"

    attrs = effect.get("attrs")
    if isinstance(attrs, dict):
        # 富属性型：写入 items dict。
        items = dict(state.get_attr(target, "items") or {})
        items[str(item)] = dict(attrs)
        context.writer.apply(SetAttr(target, "items", items))
        logger.debug("[grant_item] rich target=%s item=%s", target, item)
        return

    # 计数型：累加数量（unlimited 覆盖为无限）。
    count = effect.get("count", 1)
    if count == _UNLIMITED:
        context.writer.apply(SetAttr(target, f"{_COUNT_PREFIX}{item}", _UNLIMITED))
        return
    current = _count_of(state, target, item)
    if current == _UNLIMITED:
        return
    context.writer.apply(SetAttr(target, f"{_COUNT_PREFIX}{item}", int(current) + int(count)))
    logger.debug("[grant_item] count target=%s item=%s +%s", target, item, count)


def _handle_use_item(effect: dict, context: Any) -> None:
    """消耗某实体的一个计数型物品。

    effect 字段：
      target — 使用者，默认当前 actor。
      item   — 物品名。
      count  — 消耗数量（默认 1）。
    数量不足时断言失败（不静默）；unlimited 不减少。
    """
    state = context.state
    target = effect.get("target") or getattr(context, "actor", None)
    item = effect.get("item")
    assert target and item, "use_item 需要 target 和 item"
    current = _count_of(state, target, item)
    if current == _UNLIMITED:
        return
    count = int(effect.get("count", 1))
    assert int(current) >= count, f"{target} 的 {item} 数量不足（有 {current}，需 {count}）"
    context.writer.apply(SetAttr(target, f"{_COUNT_PREFIX}{item}", int(current) - count))
    logger.debug("[use_item] target=%s item=%s -%s", target, item, count)


def _handle_transfer_item(effect: dict, context: Any) -> None:
    """把计数型物品从一方转移给另一方（掉落/交易）。

    effect 字段：
      giver / receiver — 给出方（默认当前 actor）与接收方。
      item / count     — 物品名与数量（默认 1）。
    """
    state = context.state
    giver = effect.get("giver") or getattr(context, "actor", None)
    receiver = effect.get("receiver")
    item = effect.get("item")
    assert giver and receiver and item, "transfer_item 需要 giver、receiver 和 item"
    count = int(effect.get("count", 1))
    giver_count = _count_of(state, giver, item)
    if giver_count != _UNLIMITED:
        assert int(giver_count) >= count, f"{giver} 的 {item} 数量不足"
        context.writer.apply(SetAttr(giver, f"{_COUNT_PREFIX}{item}", int(giver_count) - count))
    receiver_count = _count_of(state, receiver, item)
    if receiver_count != _UNLIMITED:
        context.writer.apply(SetAttr(receiver, f"{_COUNT_PREFIX}{item}", int(receiver_count) + count))
    logger.debug("[transfer_item] %s->%s item=%s x%s", giver, receiver, item, count)


def _cond_has_item(spec: dict, context: dict) -> bool:
    """判断某实体是否拥有某物品（计数>0 / unlimited / 存在于 items）。

    spec.input.entity（默认当前 actor）+ spec.input.item。
    """
    state = context.get("state")
    if state is None:
        return False
    source = spec.get("input") if isinstance(spec.get("input"), dict) else spec
    entity = source.get("entity") or context.get("actor")
    item = source.get("item")
    if not entity or not item:
        return False
    count = _count_of(state, entity, item)
    if count == _UNLIMITED or (isinstance(count, int) and count > 0):
        return True
    items = state.get_attr(entity, "items") or {}
    return isinstance(items, dict) and str(item) in items


def register(api: Any) -> None:
    """把 inventory 机制注册进 PluginRegistry。"""
    api.register_effect("grant_item", _handle_grant_item)
    api.register_effect("use_item", _handle_use_item)
    api.register_effect("transfer_item", _handle_transfer_item)
    api.register_condition("inventory.has_item", _cond_has_item)


__all__ = ["register"]
