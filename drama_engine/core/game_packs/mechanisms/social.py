"""社交推理领域机制（social）。

提供计票、出局、存活计数等原子机制，供狼人杀、阿瓦隆、谁是卧底等社交推理游戏引用。

机制清单：
- effect   tally_votes  ：从 responses 统计票数，得票最高者写入 GAME.last_vote_target。
- effect   eliminate    ：把指定实体标记为出局（alive=False）。
- condition social.faction_cleared ：判断某阵营是否已被清空（存活数为 0）。
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

from drama_engine.core.dsl.components.conditions import ConditionEvaluator
from drama_engine.core.dsl.components.value_resolver import ValueResolver, parse_state_path
from drama_engine.core.engine import SetAttr

logger = logging.getLogger(__name__)

# 机制内共享的来源解析与条件求值器（无状态、可复用）。
# social.kill/record_target/build_speech_order 等需要解析 winner/data.x/@literal 来源，
# 并按 filter 过滤发言名单——与通用 EffectExecutor 用同一套 ValueResolver/ConditionEvaluator，
# 保证语义一致，且不再把这些逻辑硬编码在通用 DSL 层（M1-B）。
_VALUES = ValueResolver()
_EVAL = ConditionEvaluator()


def _resolve_source(source: Any, context: Any) -> Any:
    """解析 effect 的来源关键字（winner/actor/data.x/@literal/{ref}），复用 ValueResolver。"""
    return _VALUES.resolve(
        source,
        state=context.state,
        responses=context.responses,
        actor=context.actor,
        extra=context.extra,
    )


def _seat_sort_key(name: str, state: Any) -> tuple:
    """按 seat_index 排序；缺失时用 Player_N 的数字后缀兜底。"""
    seat_index = state.get_attr(name, "seat_index")
    if seat_index is not None:
        return (0, int(seat_index), name)
    match = re.search(r"(\d+)$", name)
    if match:
        return (1, int(match.group(1)), name)
    return (2, name)


def _resolve_path_target(effect: dict, context: Any) -> tuple[str, str]:
    """解析 path 或 entity+attr 形式的写入位置，返回 (entity, attr)。"""
    if "path" in effect:
        entity, attr = parse_state_path(effect["path"])
        resolved = _VALUES.resolve_entity(entity, context.state, context.responses, context.actor, None, context.extra)
        return resolved, attr
    entity = _VALUES.resolve_entity(effect["entity"], context.state, context.responses, context.actor, None, context.extra)
    return entity, effect["attr"]


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

    effect 字段（按优先级）：
      target — 出局对象，可为字面量名或 {ref: "ENTITY.attr"}（从状态解析）。
      from   — 状态路径 "ENTITY.attr"，从该路径读出局对象。
      缺省时读 GAME.last_vote_target。
    """
    target = _resolve_target(effect, context.state)
    if not target:
        return
    context.writer.apply(SetAttr(target, "alive", False))
    logger.debug("[eliminate] target=%s", target)


def _resolve_target(effect: dict, state: Any) -> Any:
    """解析出局对象：支持字面量 / {ref: path} / from 路径 / 默认 last_vote_target。"""
    target = effect.get("target")
    if isinstance(target, dict) and "ref" in target:
        return _read_state_path(state, str(target["ref"]))
    if target:
        return target
    from_path = effect.get("from")
    if isinstance(from_path, str) and "." in from_path:
        return _read_state_path(state, from_path)
    return state.get_attr("GAME", "last_vote_target")


def _read_state_path(state: Any, path: str) -> Any:
    """读取 "ENTITY.attr" 状态路径的值。"""
    if "." not in path:
        return None
    entity, attr = path.split(".", 1)
    return state.get_attr(entity, attr)


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


def _handle_resolve_night(effect: dict, context: Any) -> None:
    """结算夜晚死亡：狼刀目标在未被守护/解药救下时出局。

    读取状态：
      GAME.night_target — 狼刀目标。
      GAME.guard_target — 守卫本晚守护对象（相同则免死）。
      GAME.witch_save   — 女巫是否对刀口用解药（True 则免死）。
    写入：目标 alive=False（若死亡）、GAME.night_deaths 记录本晚死亡列表，并清空当晚标记。
    """
    state = context.state
    target = state.get_attr("GAME", "night_target")
    guard_target = state.get_attr("GAME", "guard_target")
    witch_save = bool(state.get_attr("GAME", "witch_save"))
    deaths: list[Any] = []
    if target and not witch_save and target != guard_target:
        context.writer.apply(SetAttr(target, "alive", False))
        deaths.append(target)
    context.writer.apply(SetAttr("GAME", "night_deaths", deaths))
    # 清空当晚一次性标记，避免残留到下一晚。
    context.writer.apply(SetAttr("GAME", "night_target", None))
    context.writer.apply(SetAttr("GAME", "guard_target", None))
    context.writer.apply(SetAttr("GAME", "witch_save", False))
    logger.debug("[resolve_night] deaths=%s", deaths)


def _handle_kill(effect: dict, context: Any) -> None:
    """杀死目标实体：设 alive=False、death_cause、death_round（social 领域机制，M1-B）。

    effect 字段：target（来源关键字 winner/actor/data.x）、cause（默认 unknown）。
    """
    target = _resolve_source(effect["target"], context)
    if target is None:
        return
    cause = effect.get("cause", "unknown")
    current_round = context.state.get_attr("GAME", "round") or 0
    context.writer.apply(SetAttr(target, "alive", False))
    context.writer.apply(SetAttr(target, "death_cause", cause))
    context.writer.apply(SetAttr(target, "death_round", current_round))


def _handle_record_target(effect: dict, context: Any) -> None:
    """把来源实体名记录到 GAME 的指定属性（刀口/查验目标等）。

    effect 字段：attr（GAME 上属性名）、source（来源关键字）。
    """
    source = _resolve_source(effect["source"], context)
    context.writer.apply(SetAttr("GAME", effect["attr"], source))


def _handle_record_current_deaths(effect: dict, context: Any) -> None:
    """记录本轮已出局玩家列表（按座位排序），供白天发言方向参考。

    effect 字段：path 或 entity+attr（写入位置）；可选 causes（死因白名单）。
    """
    state = context.state
    current_round = state.get_attr("GAME", "round") or 0
    causes = effect.get("causes")
    if causes is not None:
        assert isinstance(causes, list), "record_current_deaths.causes 必须是列表"
    deaths = [
        name for name in state.all_entities()
        if (
            name != "GAME"
            and state.get_attr(name, "death_round") == current_round
            and (causes is None or state.get_attr(name, "death_cause") in causes)
        )
    ]
    deaths.sort(key=lambda name: _seat_sort_key(name, state))
    entity, attr = _resolve_path_target(effect, context)
    context.writer.apply(SetAttr(entity, attr, deaths))


def _resolve_reference(source: Any, context: Any) -> str | None:
    """解析发言参考点；列表取座位顺序最靠前的玩家。"""
    value = _resolve_source(source, context)
    if isinstance(value, (list, tuple, set)):
        values = [item for item in value if isinstance(item, str)]
        if not values:
            return None
        values.sort(key=lambda name: _seat_sort_key(name, context.state))
        return values[0]
    if isinstance(value, str) and value:
        return value
    return None


def _handle_build_speech_order(effect: dict, context: Any) -> None:
    """按座位顺序、参考点、方向生成发言顺序（狼人杀白天发言，M1-B）。

    effect 字段：path/entity+attr（写入）、reference/fallback_reference（参考点）、
    direction（left/right/clockwise/counterclockwise，支持 data.x）、filter（默认 alive=true）。
    """
    state = context.state
    direction = _resolve_source(effect.get("direction", "left"), context)
    reference = _resolve_reference(effect.get("reference"), context)
    if reference is None:
        reference = _resolve_reference(effect.get("fallback_reference"), context)

    all_players = [name for name in state.all_entities() if name != "GAME"]
    all_players.sort(key=lambda name: _seat_sort_key(name, state))
    if not all_players:
        return
    if direction in ("right", "counterclockwise", "anticlockwise"):
        ordered_players = list(reversed(all_players))
    else:
        ordered_players = all_players

    filter_spec = effect.get("filter", {"alive": True})
    allowed_speakers = _EVAL.filter_entities(filter_spec, state)
    speakers = [name for name in ordered_players if name in allowed_speakers]
    if not speakers:
        return

    reference_index = ordered_players.index(reference) if reference in ordered_players else None
    if reference_index is None:
        ordered_speakers = speakers
    else:
        ordered_speakers = [
            name for offset in range(1, len(ordered_players) + 1)
            for name in [ordered_players[(reference_index + offset) % len(ordered_players)]]
            if name in speakers
        ]
    entity, attr = _resolve_path_target(effect, context)
    context.writer.apply(SetAttr(entity, attr, ordered_speakers))


def _cond_just_died(spec: dict, context: dict) -> bool:
    """判断某实体是否本轮死亡（social 领域条件，M1-B）。

    spec.entity / spec.input.entity 指定实体，支持 "actor"/"candidate" 关键字。
    """
    state = context.get("state")
    if state is None:
        return False
    source = spec.get("input") if isinstance(spec.get("input"), dict) else spec
    entity = str(source.get("entity") or "")
    if entity == "actor":
        entity = str(context.get("actor") or "")
    elif entity == "candidate":
        entity = str(context.get("candidate") or "")
    if not entity:
        return False
    death_round = state.get_attr(entity, "death_round")
    current_round = state.get_attr("GAME", "round")
    return death_round is not None and death_round == current_round


def _cond_is_first_round(spec: dict, context: dict) -> bool:
    """判断当前是否首轮（social 领域条件，M1-B）。

    spec.expected / spec.input.expected 指定期望值（默认 true）。
    """
    state = context.get("state")
    if state is None:
        return False
    source = spec.get("input") if isinstance(spec.get("input"), dict) else spec
    expected = bool(source.get("expected", True))
    round_num = state.get_attr("GAME", "round") or 0
    result = round_num <= 1
    return result if expected else not result


def register(api: Any) -> None:
    """把 social 机制注册进 PluginRegistry。"""
    api.register_effect("tally_votes", _handle_tally_votes)
    api.register_effect("eliminate", _handle_eliminate)
    api.register_effect("resolve_night", _handle_resolve_night)
    # M1-B：从通用 DSL 层迁入的狼人杀专属领域机制。
    api.register_effect("social.kill", _handle_kill)
    api.register_effect("social.record_target", _handle_record_target)
    api.register_effect("social.record_current_deaths", _handle_record_current_deaths)
    api.register_effect("social.build_speech_order", _handle_build_speech_order)
    api.register_condition("social.just_died", _cond_just_died)
    api.register_condition("social.is_first_round", _cond_is_first_round)
    api.register_condition("social.faction_cleared", _cond_faction_cleared)


__all__ = ["register"]


def build_social_projection_profile() -> Any:
    """构建社交推理（狼人杀等）的对外投影档案（interaction.v1 开放键富化）。

    把此前写死在 service/server/app.py 的 roleBadges/scopeStyles，以及 scene→widget
    皮肤映射，收敛为 game_pack 提供的数据。projector/StateView 消费它，前端不再读死配置。
    """
    from drama_engine.core.interaction.profile import ProjectionProfile

    return ProjectionProfile(
        # scene → 输入组件皮肤（vote/choice 之上的狼人杀专属渲染变体）。
        widget_by_scene={
            "wolf_kill": "vote:night_kill",
            "seer_check": "choice:seer_inspect",
            "witch_action": "choice:witch_potion",
            "day_vote": "vote:day_exile",
        },
        # scene → 语义级 props（A 收敛：只放影响信息可见性的参数）。
        props_by_scene={
            "wolf_kill": {"show_teammate_votes": True},
            "day_vote": {"show_vote_count": True},
        },
        # 角色内部值 → 展示名（原 app.py roleBadges）。
        role_badges={
            "werewolf": "狼人", "wolf": "狼人", "seer": "预言家", "witch": "女巫",
            "hunter": "猎人", "guard": "守卫", "villager": "村民",
        },
        # scope → [底色, 边色, 标签]（原 app.py scopeStyles）。
        scope_styles={
            "public": ["#f3f4f6", "#9ca3af", "公开"],
            "town": ["#ecfdf5", "#10b981", "城镇"],
            "wolf-den": ["#fef2f2", "#ef4444", "狼队"],
            "wolf_den": ["#fef2f2", "#ef4444", "狼队"],
            "whisper:seer": ["#eef2ff", "#6366f1", "预言家"],
            "whisper:witch": ["#f5f3ff", "#8b5cf6", "女巫"],
            "whisper:guard": ["#eff6ff", "#3b82f6", "守卫"],
        },
    )
