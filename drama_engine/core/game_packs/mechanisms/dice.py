"""骰子领域机制（dice）。

提供可回放的掷骰机制：随机种子存在 GAME.dice_seed，用线性同余推进，保证 dry-run 与
回滚可复现（不使用全局 random，便于 checkpoint/rollback 一致）。

机制清单：
- effect  roll_dice ：掷骰，结果写入 GAME.last_roll，可选累加到 target 路径。
- effect  advance_on_track ：按最近一次掷骰在环形轨道上移动 actor。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.engine import SetAttr

logger = logging.getLogger(__name__)

# 线性同余发生器参数（数值稳定、可复现）。
_LCG_A = 1103515245
_LCG_C = 12345
_LCG_M = 2 ** 31


def _next_seed(seed: int) -> int:
    """推进随机种子。"""
    return (_LCG_A * seed + _LCG_C) % _LCG_M


def _read_int(spec: dict, key: str, default: int) -> int:
    """从 effect.<key> 读整数。"""
    value = spec.get(key) if isinstance(spec, dict) else None
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _handle_roll_dice(effect: dict, context: Any) -> None:
    """掷骰并把结果写入 GAME.last_roll。

    effect 字段：
      sides — 骰子面数，默认 6。
      count — 骰子个数，默认 1。
      to    — 可选状态路径 "ENTITY.attr"，把点数累加到该属性。
    """
    state = context.state
    sides = max(1, _read_int(effect, "sides", 6))
    count = max(1, _read_int(effect, "count", 1))
    seed = state.get_attr("GAME", "dice_seed")
    seed = int(seed) if isinstance(seed, int) else 1
    total = 0
    rolls = []
    for _ in range(count):
        seed = _next_seed(seed)
        value = (seed % sides) + 1
        rolls.append(value)
        total += value
    context.writer.apply(SetAttr("GAME", "dice_seed", seed))
    context.writer.apply(SetAttr("GAME", "last_roll", total))
    context.writer.apply(SetAttr("GAME", "last_rolls", rolls))
    to_path = effect.get("to")
    if isinstance(to_path, str) and "." in to_path:
        entity, attr = to_path.split(".", 1)
        current = state.get_attr(entity, attr) or 0
        context.writer.apply(SetAttr(entity, attr, int(current) + total))
    logger.debug("[roll_dice] rolls=%s total=%s", rolls, total)


def _handle_advance_on_track(effect: dict, context: Any) -> None:
    """按最近一次掷骰在环形轨道上移动 actor。

    effect 字段：
      track_size — 轨道格子数，默认读 GAME.board_size。
      actor      — 移动对象，默认当前 actor。
      steps      — 覆盖步数；缺省时用 GAME.last_roll。
    写入：<actor>.position（0..track_size-1），经过起点时写 GAME.passed_start=True。
    """
    state = context.state
    actor = effect.get("actor") or getattr(context, "actor", None)
    assert actor, "advance_on_track 需要 actor"
    track_size = _read_int(effect, "track_size", int(state.get_attr("GAME", "board_size") or 0))
    assert track_size > 0, "advance_on_track 需要正的 track_size 或 GAME.board_size"
    steps = effect.get("steps")
    steps = int(steps) if steps is not None else int(state.get_attr("GAME", "last_roll") or 0)
    current = int(state.get_attr(actor, "position") or 0)
    new_position = (current + steps) % track_size
    passed_start = (current + steps) >= track_size
    context.writer.apply(SetAttr(actor, "position", new_position))
    context.writer.apply(SetAttr("GAME", "passed_start", bool(passed_start)))
    logger.debug("[advance_on_track] actor=%s %s->%s passed_start=%s", actor, current, new_position, passed_start)


def register(api: Any) -> None:
    """把 dice 机制注册进 PluginRegistry。"""
    api.register_effect("roll_dice", _handle_roll_dice)
    api.register_effect("advance_on_track", _handle_advance_on_track)


__all__ = ["register"]
