"""骰子领域机制（dice）。

提供可回放的掷骰机制：随机种子存在 GAME.dice_seed，用线性同余推进，保证 dry-run 与
回滚可复现（不使用全局 random，便于 checkpoint/rollback 一致）。

支持三种掷骰方式，可组合：
1. 标准骰：sides（面数）+ count（个数），掷 1..sides 均匀值。
2. 自定义面值 + 加权概率：faces（任意值集）+ 可选 weights（每面权重）。
3. 具名骰子：在 GAME.dice_defs 里按 id 定义骰子，effect 用 die/dice 引用；
   同一局可定义多个骰子，不同场景投不同骰子，一次也可投多个。

机制清单：
- effect  roll_dice ：掷骰，结果写入 GAME.last_roll(总和)/GAME.last_rolls(明细)，
  可选累加到 to 状态路径。
- effect  advance_on_track ：按最近一次掷骰在环形轨道上移动 actor。

GAME.dice_defs 形如：
  { "attack": {"faces": ["hit", "miss"], "weights": [0.3, 0.7]},
    "d20":    {"sides": 20},
    "catan":  {"faces": [0, 0, 1, 1, 2, 5]} }
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


def _resolve_die_specs(effect: dict, state: Any) -> list[dict]:
    """把 effect 解析成一批「单个骰子规格」列表，供逐个掷。

    优先级：
      dice: [id, id, ...]  → 从 GAME.dice_defs 取多个具名骰子。
      die: id              → 从 GAME.dice_defs 取一个具名骰子。
      内联 faces/weights/sides + count → 生成 count 个相同的内联骰子。
    返回的每一项都是 {"faces": [...], "weights": [...]|None} 或 {"sides": int}。
    """
    defs = state.get_attr("GAME", "dice_defs")
    defs = defs if isinstance(defs, dict) else {}

    dice_ref = effect.get("dice")
    if isinstance(dice_ref, list) and dice_ref:
        specs = []
        for die_id in dice_ref:
            assert str(die_id) in defs, f"未定义的骰子 id: {die_id}（请在 GAME.dice_defs 中声明）"
            specs.append(dict(defs[str(die_id)]))
        return specs

    die_ref = effect.get("die")
    if die_ref is not None:
        assert str(die_ref) in defs, f"未定义的骰子 id: {die_ref}（请在 GAME.dice_defs 中声明）"
        return [dict(defs[str(die_ref)])]

    # 内联骰子：faces 优先于 sides。
    count = max(1, _read_int(effect, "count", 1))
    if isinstance(effect.get("faces"), list) and effect["faces"]:
        one = {"faces": list(effect["faces"])}
        if isinstance(effect.get("weights"), list):
            one["weights"] = list(effect["weights"])
        return [dict(one) for _ in range(count)]
    sides = max(1, _read_int(effect, "sides", 6))
    return [{"sides": sides} for _ in range(count)]


def _roll_one(die: dict, seed: int) -> tuple[Any, int]:
    """按单个骰子规格掷一次，返回 (点数, 新种子)。

    - 有 faces：从值集中按 weights 加权抽样（无 weights 则等概率）。
    - 否则按 sides 掷 1..sides 均匀值。
    抽样只用传入种子推进，保证可回放。
    """
    seed = _next_seed(seed)
    faces = die.get("faces")
    if isinstance(faces, list) and faces:
        weights = die.get("weights")
        if isinstance(weights, list) and len(weights) == len(faces) and sum(weights) > 0:
            # 加权抽样：把 [0, total) 均分成 seed 的一个投影点，落在哪个区间取哪个面。
            total = float(sum(weights))
            point = (seed % _LCG_M) / _LCG_M * total
            cumulative = 0.0
            for face, weight in zip(faces, weights):
                cumulative += float(weight)
                if point < cumulative:
                    return face, seed
            return faces[-1], seed
        return faces[seed % len(faces)], seed
    sides = max(1, int(die.get("sides") or 6))
    return (seed % sides) + 1, seed


def _handle_roll_dice(effect: dict, context: Any) -> None:
    """掷骰并把结果写入 GAME.last_roll / GAME.last_rolls。

    effect 字段：
      sides / count      — 标准骰面数与个数（默认 6 面 1 个）。
      faces / weights    — 自定义面值与加权概率（faces 优先于 sides）。
      die / dice         — 引用 GAME.dice_defs 里的具名骰子（单个 / 多个）。
      to                 — 可选状态路径 "ENTITY.attr"，把数值总和累加到该属性。
    数值面（int）会求和写入 GAME.last_roll；非数值面（如字符串）总和记 0，明细仍在
    GAME.last_rolls 中，供 referee/condition 判定。
    """
    state = context.state
    seed = state.get_attr("GAME", "dice_seed")
    seed = int(seed) if isinstance(seed, int) else 1

    die_specs = _resolve_die_specs(effect, state)
    rolls: list[Any] = []
    total = 0
    for die in die_specs:
        value, seed = _roll_one(die, seed)
        rolls.append(value)
        if isinstance(value, bool):
            continue  # bool 不计入数值总和
        if isinstance(value, (int, float)):
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
