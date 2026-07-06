"""卡牌领域机制（cards）。

提供摸牌、出牌、手牌清空判断等原子机制，供 UNO、爆炸猫、德州扑克等卡牌游戏引用。
牌堆存 GAME.deck（list），弃牌堆存 GAME.discard（list），手牌存 <entity>.hand（list）。

机制清单：
- effect   draw_card  ：从牌堆顶给某实体摸 count 张牌。
- effect   play_card  ：把某实体手中一张牌打到弃牌堆顶。
- condition cards.hand_empty ：判断某实体手牌是否为空（清空手牌胜利条件）。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.engine import SetAttr

logger = logging.getLogger(__name__)


def _as_list(value: Any) -> list:
    """把状态值安全转成 list 副本。"""
    return list(value) if isinstance(value, list) else []


def _handle_draw_card(effect: dict, context: Any) -> None:
    """从牌堆顶给某实体摸牌。

    effect 字段：
      target — 摸牌对象，默认当前 actor。
      count  — 摸牌数，默认 1。
    牌堆空时停止摸牌（不报错，交由游戏规则处理）。
    """
    state = context.state
    target = effect.get("target") or getattr(context, "actor", None)
    assert target, "draw_card 需要 target"
    count = int(effect.get("count") or 1)
    deck = _as_list(state.get_attr("GAME", "deck"))
    hand = _as_list(state.get_attr(target, "hand"))
    drawn = 0
    for _ in range(count):
        if not deck:
            break
        hand.append(deck.pop(0))
        drawn += 1
    context.writer.apply(SetAttr("GAME", "deck", deck))
    context.writer.apply(SetAttr(target, "hand", hand))
    logger.debug("[draw_card] target=%s drawn=%s remain_deck=%s", target, drawn, len(deck))


def _handle_play_card(effect: dict, context: Any) -> None:
    """把某实体手里的一张牌打到弃牌堆顶。

    effect 字段：
      target — 出牌对象，默认当前 actor。
      card   — 要打出的牌；缺省时读当前 response.data.card。
    """
    state = context.state
    target = effect.get("target") or getattr(context, "actor", None)
    assert target, "play_card 需要 target"
    card = effect.get("card")
    if card is None:
        responses = getattr(context, "responses", None) or []
        if responses:
            data = responses[0].get("data") if isinstance(responses[0], dict) else None
            card = (data or {}).get("card") if isinstance(data, dict) else None
    assert card is not None, "play_card 需要 card 或当前 response.data.card"
    hand = _as_list(state.get_attr(target, "hand"))
    assert card in hand, f"{target} 手中没有这张牌: {card}"
    hand.remove(card)
    discard = _as_list(state.get_attr("GAME", "discard"))
    discard.append(card)
    context.writer.apply(SetAttr(target, "hand", hand))
    context.writer.apply(SetAttr("GAME", "discard", discard))
    context.writer.apply(SetAttr("GAME", "top_card", card))
    logger.debug("[play_card] target=%s card=%s hand_left=%s", target, card, len(hand))


def _cond_hand_empty(spec: dict, context: dict) -> bool:
    """判断某实体手牌是否为空。spec.input.entity / spec.entity 指定，缺省用当前 actor。"""
    state = context.get("state")
    if state is None:
        return False
    source = spec.get("input") if isinstance(spec.get("input"), dict) else spec
    entity = source.get("entity") or context.get("actor")
    if not entity:
        return False
    return len(_as_list(state.get_attr(entity, "hand"))) == 0


def register(api: Any) -> None:
    """把 cards 机制注册进 PluginRegistry。"""
    api.register_effect("draw_card", _handle_draw_card)
    api.register_effect("play_card", _handle_play_card)
    api.register_condition("cards.hand_empty", _cond_hand_empty)


__all__ = ["register"]
