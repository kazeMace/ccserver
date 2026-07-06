"""社交推理领域机制（social）。

提供计票、出局、存活计数等原子机制，供狼人杀、阿瓦隆、谁是卧底等社交推理游戏引用。

机制清单：
- effect   tally_votes  ：从 responses 统计票数，得票最高者写入 GAME.last_vote_target。
- effect   eliminate    ：把指定实体标记为出局（alive=False）。
- condition social.faction_cleared ：判断某阵营是否已被清空（存活数为 0）。
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from drama_engine.core.engine import SetAttr

logger = logging.getLogger(__name__)


def _handle_tally_votes(effect: dict, context: Any) -> None:
    """统计 responses 中的投票，把最高票写入目标状态路径。

    effect 字段：
      field — response.data 中的投票字段名，默认 "vote"。
      to    — 结果写入的状态路径，默认 "GAME.last_vote_target"。
      tie   — 平票策略：no_winner(默认) / first。
    """
    field = effect.get("field", "vote")
    to_path = effect.get("to", "GAME.last_vote_target")
    tie = effect.get("tie", "no_winner")
    counter: Counter = Counter()
    for response in getattr(context, "responses", None) or []:
        data = response.get("data") if isinstance(response, dict) else None
        target = (data or {}).get(field) if isinstance(data, dict) else None
        if target:
            counter[str(target)] += 1
    winner = _pick_winner(counter, tie)
    entity, attr = _split_path(to_path)
    context.writer.apply(SetAttr(entity, attr, winner))
    logger.debug("[tally_votes] counter=%s winner=%s", dict(counter), winner)


def _pick_winner(counter: Counter, tie: str) -> Any:
    """按平票策略选出胜者；无票或平票且 no_winner 时返回 None。"""
    if not counter:
        return None
    ranked = counter.most_common()
    top_count = ranked[0][1]
    top = [name for name, count in ranked if count == top_count]
    if len(top) == 1:
        return top[0]
    if tie == "first":
        return top[0]
    return None


def _handle_eliminate(effect: dict, context: Any) -> None:
    """把指定实体标记为出局。

    effect 字段：
      target — 出局对象；缺省时读 GAME.last_vote_target。
    """
    target = effect.get("target")
    if not target:
        target = context.state.get_attr("GAME", "last_vote_target")
    if not target:
        return
    context.writer.apply(SetAttr(target, "alive", False))
    logger.debug("[eliminate] target=%s", target)


def _cond_faction_cleared(spec: dict, context: dict) -> bool:
    """判断某阵营存活数是否为 0。

    spec.input.faction / spec.faction 指定阵营名；按 <entity>.role == faction 且 alive
    统计。players 从 GAME.players 读取。
    """
    state = context.get("state")
    if state is None:
        return False
    source = spec.get("input") if isinstance(spec.get("input"), dict) else spec
    faction = source.get("faction")
    attr = source.get("attr", "role")
    if not faction:
        return False
    players = state.get_attr("GAME", "players") or []
    alive_count = 0
    for name in players:
        if state.get_attr(name, attr) == faction and state.get_attr(name, "alive") is not False:
            alive_count += 1
    return alive_count == 0


def _split_path(path: str) -> tuple[str, str]:
    """把 "ENTITY.attr" 拆成 (entity, attr)。"""
    assert isinstance(path, str) and "." in path, f"状态路径必须是 ENTITY.attr: {path}"
    entity, attr = path.split(".", 1)
    return entity, attr


def register(api: Any) -> None:
    """把 social 机制注册进 PluginRegistry。"""
    api.register_effect("tally_votes", _handle_tally_votes)
    api.register_effect("eliminate", _handle_eliminate)
    api.register_condition("social.faction_cleared", _cond_faction_cleared)


__all__ = ["register"]
