"""Dialogue policies and scene-level action helpers."""

import asyncio
import random
from typing import Any

from .constants import MAX_COLLECT_RETRIES, MAX_LOOP_TURNS
from .cast import Cast
from .models import Scene
from .stage import Stage
from .state import State


def _order_participant_names(participant_names, cast: Cast) -> list[str]:
    """
    将 scene.participants(state) 的结果转成可执行顺序。

    参数：
      participant_names — set/list/tuple，来自 Scene.participants。
      cast — 当前演员池，用于过滤不存在的 Actor。

    返回：
      Actor 名称列表。list/tuple 会保留脚本给出的顺序；set 使用 Cast 注册顺序。
    """
    cast_names = set(cast.all_names())
    if isinstance(participant_names, (list, tuple)):
        return [name for name in participant_names if name in cast_names]

    return [
        name for name in cast.all_names()
        if name in participant_names
    ]


async def _wait_scene_gate(scene: Scene) -> None:
    """等待 Director 注入到 scene 上的暂停/单步闸门。"""
    gate = getattr(scene, "_gate", None)
    if gate is not None:
        await gate.wait()


class Sequential:
    """
    顺次发言策略 — participants 按顺序依次 act，每人发言后立刻投递。

    适用场景：白天轮流讨论、遗言发言等。
    """

    async def run(self, scene: Scene, stage: Stage, state: State, cast: Cast) -> list:
        """
        让 participants 按顺序逐一发言。

        参数：
          scene — 当前幕（包含 participants / cue / response_model 等信息）
          stage — 舞台（用于投递发言）
          state — 世界状态
          cast  — 演员池

        返回：
          所有 Response 字典的列表
        """
        responses = []

        # 求值当前参与者集合（拉取式）
        participant_names = scene.participants(state)

        ordered_names = _order_participant_names(participant_names, cast)

        for name in ordered_names:
            await _wait_scene_gate(scene)
            actor = cast.get(name)
            _prepare_actor_for_scene(actor, scene, state)
            cue_text = _resolve_action_cue(scene, state, actor_name=name)
            response = await _act_with_candidate_validation(
                actor=actor,
                scene=scene,
                state=state,
                cue_text=cue_text,
            )
            delivery_text = _prepare_response_for_delivery(
                actor=actor,
                scene=scene,
                state=state,
                response=response,
            )
            await _deliver_self_message(actor, scene, response)
            responses.append(response)

            # 立刻把发言投递给 Scope 内的其他人（让他们 perceive）
            if delivery_text:
                await stage.deliver(
                    msg={"sender": name, "text": delivery_text},
                    scope_name=scene.scope,
                    state=state,
                    exclude={name},   # 发言人不收到自己的发言
                )

            print(f"[Sequential] {name} 发言：{response['text'][:80]}")

        return responses


class Simultaneous:
    """
    同时发言策略 — 所有 participants 同时 act（asyncio.gather 并发），
    结束后一起公布发言结果。

    适用场景：投票（防止后投的人受前面影响跟票）。

    delivery="deferred" 时，所有人发言完再统一投递。
    """

    async def run(self, scene: Scene, stage: Stage, state: State, cast: Cast) -> list:
        """
        让 participants 同时发言，全部完成后再公布。

        参数：同 Sequential.run

        返回：
          所有 Response 字典的列表
        """
        participant_names = scene.participants(state)
        ordered_names = _order_participant_names(participant_names, cast)

        # 用 asyncio.gather 让所有人同时 act
        # 注意：每个 actor.act() 会独立清空自己的缓冲并调用 LLM
        actors = [cast.get(name) for name in ordered_names]
        for actor in actors:
            _prepare_actor_for_scene(actor, scene, state)
        await _wait_scene_gate(scene)
        results = await asyncio.gather(
            *[
                _act_with_candidate_validation(
                    actor=actor,
                    scene=scene,
                    state=state,
                    cue_text=_resolve_action_cue(scene, state, actor_name=actor.name),
                )
                for actor in actors
            ]
        )
        responses = list(results)

        # 所有人发言完毕后，一起投递（公布结果）
        for response in responses:
            await _wait_scene_gate(scene)
            sender_name = response["actor"]
            actor = cast.get(sender_name)
            delivery_text = _prepare_response_for_delivery(
                actor=actor,
                scene=scene,
                state=state,
                response=response,
            )
            await _deliver_self_message(actor, scene, response)
            if delivery_text:
                await stage.deliver(
                    msg={"sender": sender_name, "text": delivery_text},
                    scope_name=scene.scope,
                    state=state,
                    exclude={sender_name},
                )
            print(f"[Simultaneous] {sender_name} 发言：{response['text'][:80]}")

        return responses


class Single:
    """
    单人发言策略 — 只让 participants 中的第一个人 act。

    适用场景：预言家查验、女巫用药、遗言等只有一人发言的幕。
    """

    async def run(self, scene: Scene, stage: Stage, state: State, cast: Cast) -> list:
        """
        让 participants 中的第一个人发言。

        参数：同 Sequential.run

        返回：
          包含 1 个 Response 的列表（participants 为空时返回空列表）
        """
        participant_names = scene.participants(state)

        if not participant_names:
            return []

        # 从可执行顺序中取第一个上场者。
        ordered_names = _order_participant_names(participant_names, cast)
        first_name = ordered_names[0] if ordered_names else None

        if first_name is None:
            return []

        actor = cast.get(first_name)
        _prepare_actor_for_scene(actor, scene, state)
        cue_text = _resolve_action_cue(scene, state, actor_name=first_name)
        await _wait_scene_gate(scene)
        response = await _act_with_candidate_validation(
            actor=actor,
            scene=scene,
            state=state,
            cue_text=cue_text,
        )
        delivery_text = _prepare_response_for_delivery(
            actor=actor,
            scene=scene,
            state=state,
            response=response,
        )
        await _deliver_self_message(actor, scene, response)

        if delivery_text:
            await stage.deliver(
                msg={"sender": first_name, "text": delivery_text},
                scope_name=scene.scope,
                state=state,
                exclude={first_name},
            )

        print(f"[Single] {first_name} 发言：{response['text'][:80]}")
        return [response]


class RandomOrder:
    """
    随机顺序发言策略 — participants 以随机顺序依次 act。

    适用场景：白天自由讨论（避免「发言顺序」固化影响策略）。
    """

    async def run(self, scene: Scene, stage: Stage, state: State, cast: Cast) -> list:
        """
        让 participants 以随机顺序依次发言。

        参数：同 Sequential.run

        返回：
          所有 Response 字典的列表（顺序随机）
        """
        participant_names = scene.participants(state)
        ordered_names = _order_participant_names(participant_names, cast)

        # 打乱顺序
        shuffled_names = ordered_names[:]
        random.shuffle(shuffled_names)

        responses = []

        for name in shuffled_names:
            await _wait_scene_gate(scene)
            actor = cast.get(name)
            _prepare_actor_for_scene(actor, scene, state)
            cue_text = _resolve_action_cue(scene, state, actor_name=name)
            response = await _act_with_candidate_validation(
                actor=actor,
                scene=scene,
                state=state,
                cue_text=cue_text,
            )
            delivery_text = _prepare_response_for_delivery(
                actor=actor,
                scene=scene,
                state=state,
                response=response,
            )
            await _deliver_self_message(actor, scene, response)
            responses.append(response)

            if delivery_text:
                await stage.deliver(
                    msg={"sender": name, "text": delivery_text},
                    scope_name=scene.scope,
                    state=state,
                    exclude={name},
                )
            print(f"[RandomOrder] {name} 发言：{response['text'][:80]}")

        return responses


class OpenChat:
    """
    自由群聊策略 — participants 在一个 scene 内进行多轮自由发言。

    OpenChat 只是 PartySessionRuntime 的一种 dialogue_policy.mode，
    与 group_chat 执行模型无关。它不会接管运行时、解释器或消息循环，只负责
    当前 scene 内如何调度 actor 发言。
    """

    def __init__(self, rounds: int = 1, speakers_per_round: int | None = None) -> None:
        """
        初始化自由群聊策略。

        参数：
          rounds             — 群聊轮数，至少 1。
          speakers_per_round — 每轮随机发言人数；None 表示本轮所有参与者都可发言。
        """
        assert isinstance(rounds, int) and rounds >= 1, "OpenChat.rounds 必须是正整数"
        assert speakers_per_round is None or (
            isinstance(speakers_per_round, int) and speakers_per_round >= 1
        ), "OpenChat.speakers_per_round 必须是正整数或 None"
        self.rounds = rounds
        self.speakers_per_round = speakers_per_round

    async def run(self, scene: Scene, stage: Stage, state: State, cast: Cast) -> list:
        """
        让 participants 进行多轮自由群聊。

        每轮开始时重新求值 participants，随机选择本轮发言者，并在每个发言后
        立即投递给 scene.scope 中其他成员，从而形成“七嘴八舌”的上下文流。
        """
        all_responses = []

        for round_index in range(self.rounds):
            participant_names = scene.participants(state)
            ordered_names = _order_participant_names(participant_names, cast)
            if not ordered_names:
                break

            shuffled_names = ordered_names[:]
            random.shuffle(shuffled_names)
            if self.speakers_per_round is not None:
                shuffled_names = shuffled_names[:self.speakers_per_round]

            for name in shuffled_names:
                await _wait_scene_gate(scene)
                actor = cast.get(name)
                _prepare_actor_for_scene(actor, scene, state)
                cue_text = _resolve_action_cue(scene, state, actor_name=name)
                response = await _act_with_candidate_validation(
                    actor=actor,
                    scene=scene,
                    state=state,
                    cue_text=cue_text,
                )
                delivery_text = _prepare_response_for_delivery(
                    actor=actor,
                    scene=scene,
                    state=state,
                    response=response,
                )
                await _deliver_self_message(actor, scene, response)
                all_responses.append(response)

                if delivery_text:
                    await stage.deliver(
                        msg={"sender": name, "text": delivery_text},
                        scope_name=scene.scope,
                        state=state,
                        exclude={name},
                    )
                print(
                    f"[OpenChat] 第 {round_index + 1} 轮 {name} 发言："
                    f"{response['text'][:80]}"
                )

        return all_responses


class LoopUntil:
    """
    循环发言策略 — 让 participants 轮流发言，直到满足某个条件才停止。

    适用场景：狼人讨论刀谁（循环直到达成共识）。

    有最大轮次保护（MAX_LOOP_TURNS），防止无限循环。
    """

    def __init__(self, condition):
        """
        初始化循环发言策略。

        参数：
          condition — 退出条件函数：def fn(responses, state) -> bool
                      返回 True 时停止循环
        """
        self.condition = condition

    async def run(self, scene: Scene, stage: Stage, state: State, cast: Cast) -> list:
        """
        让 participants 循环发言，直到满足退出条件。

        参数：同 Sequential.run

        返回：
          所有轮次的 Response 字典列表（扁平合并）
        """
        all_responses = []
        turn_count = 0

        while turn_count < MAX_LOOP_TURNS:
            turn_count += 1
            participant_names = scene.participants(state)

            if not participant_names:
                break

            ordered_names = _order_participant_names(participant_names, cast)

            round_responses = []

            for name in ordered_names:
                await _wait_scene_gate(scene)
                actor = cast.get(name)
                _prepare_actor_for_scene(actor, scene, state)
                cue_text = _resolve_action_cue(scene, state, actor_name=name)
                response = await _act_with_candidate_validation(
                    actor=actor,
                    scene=scene,
                    state=state,
                    cue_text=cue_text,
                )
                delivery_text = _prepare_response_for_delivery(
                    actor=actor,
                    scene=scene,
                    state=state,
                    response=response,
                )
                await _deliver_self_message(actor, scene, response)
                round_responses.append(response)

                if delivery_text:
                    await stage.deliver(
                        msg={"sender": name, "text": delivery_text},
                        scope_name=scene.scope,
                        state=state,
                        exclude={name},
                    )

            all_responses.extend(round_responses)

            print(f"[LoopUntil] 第 {turn_count} 轮，检查退出条件...")

            # 检查退出条件
            if self.condition(round_responses, state):
                print(f"[LoopUntil] 满足退出条件，停止循环")
                break

        if turn_count >= MAX_LOOP_TURNS:
            print(f"[LoopUntil] 达到最大轮次 {MAX_LOOP_TURNS}，强制停止")

        return all_responses


class Narration:
    """
    旁白策略 — 不调度任何人发言，只让旁白发出提示词（cue）。

    适用场景：
      - 平安夜公告
      - 天黑/天亮提示
      - 死亡公告
    """

    async def run(self, scene: Scene, stage: Stage, state: State, cast: Cast) -> list:
        """
        不调度发言，返回空列表。
        旁白词已由 Director 通过 Narrator.say() 投递出去了。

        返回：
          空列表
        """
        return []


async def _deliver_self_message(actor: Any, scene: Scene, response: dict) -> None:
    """
    把 messages target=self 的文本写回行动者自己的感知缓冲。

    这一步让行动者下一次 act 时看到剧本世界文本，而不是只依赖底层
    Agent 历史里可能存在的结构化 JSON。
    """
    text = response.get("text")
    if not text:
        return
    await actor.perceive({
        "scope": scene.scope,
        "sender": response.get("actor", getattr(actor, "name", "")),
        "text": text,
    })


def _prepare_response_for_delivery(
    actor: Any,
    scene: Scene,
    state: State,
    response: dict,
) -> str:
    """
    把 Actor 的原始响应整理成人可读、可投递的剧本世界文本。

    规则层仍读取 response["data"]；感知层和观测层使用这里渲染出的文本。
    这样结构化 JSON 不会直接进入其他 Actor 的上下文。
    """
    raw_text = response.get("text", "")
    response["raw_text"] = raw_text

    routed_messages = _render_scene_messages(scene, state, response)
    self_text = routed_messages.get("self")
    audience_text = routed_messages.get("scope")
    observer_text = routed_messages.get("observer")
    debug_text = routed_messages.get("debug")

    response["text"] = self_text
    response["scope_text"] = audience_text
    response["observer_text"] = observer_text
    response["debug_text"] = debug_text
    _record_actor_act(actor, observer_text or self_text)
    return audience_text


def _record_actor_act(actor: Any, text: str) -> None:
    """把格式化后的行动文本写入观测旁路。"""
    tracer = getattr(actor, "_tracer", None)
    if tracer is not None:
        tracer.record_act(actor=actor.name, text=text)


def _render_scene_messages(scene: Scene, state: State, response: dict) -> dict:
    """
    按 scene.response_messages 规则渲染所有投递目标的文本。

    返回：
      {"self": str, "scope": str, "observer": str, "debug": str}
    """
    result = {}
    rules = _normalize_message_rules(scene)

    for rule in rules:
        if not _message_rule_applies(rule, response, state):
            continue

        base_render = rule.get("render")
        targets = _normalize_message_targets(rule.get("targets"))
        for target in targets:
            target_when = target.get("when")
            if target_when is not None and not _message_when_matches(target_when, response, state):
                continue
            target_name = target["to"]
            template = target.get("render", base_render)
            if template is None:
                continue
            if template == "":
                result[target_name] = ""
            else:
                result[target_name] = _render_message_template(template, scene, state, response)

    # 没有自定义规则时使用默认渲染，避免结构化 JSON 泄露。
    # 有自定义规则时，不再给缺失的 scope 自动补默认文本：脚本作者
    # 可能是有意不公开某个动作，例如猎人选择不开枪。
    if not rules:
        result.setdefault("self", _default_response_text(scene, response, "self"))
        result.setdefault("scope", _default_response_text(scene, response, "scope"))
        result.setdefault("observer", result.get("self", ""))
    else:
        result.setdefault("self", _default_response_text(scene, response, "self"))
        result.setdefault("observer", result.get("self", ""))
    return result


def _normalize_message_rules(scene: Scene) -> list:
    """把 scene.response_messages 统一成 messages 规则列表。"""
    if scene.response_messages is not None:
        if isinstance(scene.response_messages, list):
            return [rule for rule in scene.response_messages if isinstance(rule, dict)]
        if isinstance(scene.response_messages, dict):
            if "render" in scene.response_messages or "targets" in scene.response_messages:
                return [scene.response_messages]
            if "action" in scene.response_messages:
                action_rule = scene.response_messages["action"]
                if isinstance(action_rule, dict):
                    return [action_rule]
        if isinstance(scene.response_messages, str):
            return [{
                "source": "action",
                "render": scene.response_messages,
                "targets": ["self", "scope", "observer"],
            }]

    return []


def _message_rule_applies(rule: dict, response: dict, state: State) -> bool:
    """判断消息规则是否适用于当前响应。

    支持两层判断：
      1. source 判断响应类型；
      2. when 判断响应数据，例如只在 data.action=true 时公开猎人开枪结果。
    """
    source = rule.get("source", "action")
    data = response.get("data")
    if source == "action":
        source_applies = isinstance(data, dict)
    elif source == "raw":
        source_applies = bool(response.get("raw_text") or response.get("text"))
    elif source == "all":
        source_applies = True
    else:
        source_applies = False

    if not source_applies:
        return False

    when_spec = rule.get("when")
    if when_spec is None:
        return True
    return _message_when_matches(when_spec, response, state)


def _message_when_matches(when_spec: Any, response: dict, state: State) -> bool:
    """求值 messages.when 条件。

    这里刻意只实现消息路由需要的简单条件，避免把完整场景条件求值器
    反向耦合进 engine.py。支持 all/any/not、value/ref、equal/not_equal、
    is_null/not_null，足够表达“只在猎人确认开枪时公开结果”。
    """
    assert isinstance(when_spec, dict), f"messages.when 必须是 dict，收到 {type(when_spec)}"

    if "all" in when_spec:
        return all(_message_when_matches(item, response, state) for item in when_spec["all"])
    if "any" in when_spec:
        return any(_message_when_matches(item, response, state) for item in when_spec["any"])
    if "not" in when_spec:
        return not _message_when_matches(when_spec["not"], response, state)

    value = _resolve_message_value(when_spec.get("value"), response, state)
    if "equal" in when_spec:
        return value == _resolve_message_value(when_spec.get("equal"), response, state)
    if "not_equal" in when_spec:
        return value != _resolve_message_value(when_spec.get("not_equal"), response, state)
    if "is_null" in when_spec:
        return (value is None) == bool(when_spec.get("is_null"))
    if "not_null" in when_spec:
        return (value is not None) == bool(when_spec.get("not_null"))

    raise ValueError(f"未知 messages.when 条件格式: {when_spec}")


def _resolve_message_value(source: Any, response: dict, state: State) -> Any:
    """解析 messages.when 中的简单值来源。"""
    if isinstance(source, dict) and "ref" in source:
        return _resolve_message_value(source["ref"], response, state)
    if isinstance(source, dict) and "value" in source:
        return _resolve_message_value(source["value"], response, state)
    if not isinstance(source, str):
        return source

    if source == "actor":
        return response.get("actor")
    if source == "data":
        return response.get("data")
    if source.startswith("data."):
        return _get_nested_value(response.get("data"), source[5:])
    if source.startswith("response."):
        return _get_nested_value(response, source[9:])
    if source.startswith("state."):
        return _resolve_state_value(source[6:], response, state)
    if "." in source:
        state_value = _resolve_state_value(source, response, state)
        if state_value is not None:
            return state_value
    return source


def _get_nested_value(data: Any, path: str) -> Any:
    """从 dict/list 中读取 a.b.0 这类简单路径。"""
    current = data
    if path == "":
        return current
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
    return current


def _resolve_state_value(path: str, response: dict, state: State) -> Any:
    """从 State 中读取 entity.attr；entity 支持 actor。"""
    if "." not in path:
        return None
    entity, attr = path.split(".", 1)
    if entity == "actor":
        entity = response.get("actor")
    if not entity:
        return None
    return state.get_attr(entity, attr)


def _normalize_message_targets(targets: Any) -> list:
    """把 targets 统一成 [{"to": str, "render": optional str}] 列表。"""
    if targets is None:
        return [{"to": "self"}, {"to": "scope"}, {"to": "observer"}]
    if isinstance(targets, str):
        return [{"to": targets}]
    normalized = []
    if isinstance(targets, list):
        for item in targets:
            if isinstance(item, str):
                normalized.append({"to": item})
            elif isinstance(item, dict) and item.get("to"):
                normalized.append(item)
    return normalized


def _default_response_text(scene: Scene, response: dict, target: str) -> str:
    """
    没有 response_messages 模板时的兜底文本。

    自由发言保持原文；结构化动作转成自然语言，避免 JSON 泄露到上下文。
    """
    data = response.get("data")
    raw_text = response.get("raw_text") or response.get("text", "")
    if scene.response_model is None or not isinstance(data, dict):
        return raw_text

    actor_name = response.get("actor", "")
    scene_name = scene.display_name or scene.name
    prefix = f"【{actor_name}｜{scene_name}】"
    reason = data.get("reason")
    reason_text = f"理由：{reason}" if reason else ""

    if scene.name == "sheriff-join" and "action" in data:
        action_text = "我选择上警。" if data.get("action") else "我选择不上警。"
    elif "vote" in data:
        action_text = f"我投给 {data.get('vote')}。"
    elif "action" in data:
        if "target" in data:
            if data.get("action"):
                target_value = data.get("target")
                if target_value:
                    action_text = f"我选择行动，目标是 {target_value}。"
                else:
                    action_text = "我选择行动。"
            else:
                action_text = "我选择不行动。"
        else:
            action_text = f"我选择{'是' if data.get('action') else '否'}。"
    elif "target" in data:
        action_text = f"我选择 {data.get('target')}。"
    elif "choose" in data:
        action_text = f"我选择 {data.get('choose')}。"
    else:
        action_text = "我完成了本幕动作。"

    if target in ("audience", "scope") and scene.name != "sheriff-join":
        action_text = action_text.replace("我", "该玩家", 1)
    return f"{prefix}{action_text}{reason_text}"


def _render_message_template(
    template: Any,
    scene: Scene,
    state: State,
    response: dict,
) -> str:
    """渲染 message 模板中的 {actor}、{data.xxx} 等占位符。"""
    text = str(template)

    def replace(match) -> str:
        expr = match.group(1).strip()
        value = _resolve_message_expr(expr, scene, state, response)
        return "" if value is None else str(value)

    import re
    return re.sub(r"\{([^{}]+)\}", replace, text)


def _resolve_message_expr(
    expr: str,
    scene: Scene,
    state: State,
    response: dict,
) -> Any:
    """解析 message 模板里的单个表达式。"""
    if "|" in expr:
        base_expr, mapping_expr = expr.split("|", 1)
        value = _resolve_message_expr(base_expr.strip(), scene, state, response)
        return _map_message_value(value, mapping_expr)

    actor_name = response.get("actor")
    data = response.get("data") or {}

    if expr == "actor":
        return actor_name
    if expr == "scope":
        return scene.scope
    if expr == "scene.name":
        return scene.name
    if expr == "scene.display_name":
        return scene.display_name or scene.name
    if expr == "raw_text":
        return response.get("raw_text") or response.get("text", "")

    if expr.startswith("data."):
        return data.get(expr[5:])

    if expr.startswith("GAME."):
        return state.get_attr("GAME", expr[5:])

    if expr.startswith("actor.") and actor_name:
        return state.get_attr(actor_name, expr[6:])

    return None


def _map_message_value(value: Any, mapping_expr: str) -> Any:
    """
    支持轻量映射语法：

      {data.action|true:使用解药,false:不使用解药}
      {data.vote|else:弃票}
    """
    mapping = {}
    for part in mapping_expr.split(","):
        if ":" not in part:
            continue
        key, mapped_value = part.split(":", 1)
        mapping[key.strip().lower()] = mapped_value.strip()

    if isinstance(value, bool):
        key = "true" if value else "false"
    elif value is None:
        key = "none"
    else:
        key = str(value).lower()

    if key in mapping:
        return mapping[key]
    if "else" in mapping:
        return mapping["else"]
    return value


def _resolve_cue(cue: Any, state: State) -> str:
    """
    解析旁白词：如果是函数就调用，如果是字符串就直接返回。

    参数：
      cue   — str 或 Callable[[State], str]
      state — 当前世界状态

    返回：
      旁白文本字符串
    """
    if callable(cue):
        # 是函数，用当前状态求值
        return cue(state)
    else:
        # 是字符串，直接返回
        return str(cue) if cue is not None else ""


def _resolve_publication_messages(scene: Scene, state: State) -> list[dict]:
    """
    解析 publication.messages：一条 scene 可以向多个 scope 公布不同文本。

    返回：
      [{"audience": scope_name, "text": message_text}, ...]
    """
    messages = ((scene.publication or {}).get("messages") or [])
    if not messages:
        return []

    result = []
    for item in messages:
        audience = item.get("audience") or item.get("scope") or scene.scope
        text = _resolve_cue(item.get("text"), state)
        if text:
            result.append({"audience": audience, "text": text})
    return result


def _resolve_action_cue(scene: Scene, state: State, actor_name: str | None = None) -> str:
    """
    解析发给 Actor.act() 的任务提示，并附加候选集提示。

    旁白播报仍只使用原始 cue；候选集只进入行动者的任务上下文。
    """
    cue_text = _resolve_cue(scene.cue, state)
    response_prompt = getattr(scene, "response_prompt", "")
    candidates = _resolve_scene_candidates(scene, state, actor_name=actor_name)

    parts = []
    if cue_text:
        parts.append(cue_text)
    if response_prompt:
        parts.append(f"【输出要求】\n{response_prompt}")

    if candidates:
        candidate_text = "、".join(candidates)
        parts.append(f"【可选目标】{candidate_text}")

    return "\n\n".join(parts)


def _resolve_scene_candidates(
    scene: Scene,
    state: State,
    actor_name: str | None = None,
) -> list:
    """解析当前幕候选集，失败时返回空列表，不中断主流程。"""
    if not scene.candidates:
        return []
    try:
        try:
            return list(scene.candidates(state, actor_name))
        except TypeError as first_error:
            # 兼容旧的手写脚本：candidates 可能仍是 Callable[[State], list[str]]
            try:
                return list(scene.candidates(state))
            except TypeError:
                raise first_error
    except Exception as exc:
        print(f"[Scene:{scene.name}] 候选集解析失败：{exc}")
        return []


def _set_actor_scene_context(actor: Any, scene: Scene) -> None:
    """把当前 scene 元信息注入支持该接口的 Actor/Controller。"""
    if hasattr(actor, "set_scene_context"):
        actor.set_scene_context(scene.name, scene.display_name or scene.name)


def _prepare_actor_for_scene(actor: Any, scene: Scene, state: State) -> None:
    """给支持 debug 候选集的 Actor 注入本幕候选目标。"""
    actor_name = getattr(actor, "name", None)
    candidates = _resolve_scene_candidates(scene, state, actor_name=actor_name)
    if hasattr(actor, "set_candidates"):
        # 每幕都注入候选集，包括空列表。否则真人 controller 会沿用上一幕
        # candidates，导致上警这类无候选动作被错误地按上一幕目标校验。
        # Always inject candidates, including an empty list. Otherwise human
        # controllers keep stale candidates from the previous scene.
        actor.set_candidates(candidates)
    if hasattr(actor, "set_candidate_constraints"):
        constraints = dict(scene.candidate_constraints or {})
        for key in ("count", "min", "max"):
            if key in constraints:
                constraints[key] = _resolve_candidate_count_constraint(
                    constraints.get(key),
                    candidates=candidates,
                    state=state,
                )
        actor.set_candidate_constraints(constraints)


async def _act_with_candidate_validation(
    actor: Any,
    scene: Scene,
    state: State,
    cue_text: str,
) -> dict:
    """
    调用 Actor.act，并校验结构化动作目标是否属于当前候选集。

    This runtime guard keeps candidates as a real rule boundary, not only a
    prompt hint. If the actor chooses an invalid target, the actor gets a
    corrective cue and retries up to MAX_COLLECT_RETRIES times.
    """
    current_cue = cue_text
    for attempt in range(MAX_COLLECT_RETRIES):
        _set_actor_scene_context(actor, scene)
        response = await actor.act(current_cue, scene.response_model)
        error = _candidate_validation_error(
            scene=scene,
            state=state,
            actor_name=getattr(actor, "name", ""),
            response=response,
        )
        if not error:
            return response

        print(
            f"[CandidateValidation:{getattr(actor, 'name', '?')}] "
            f"第 {attempt + 1} 次候选校验失败：{error}"
        )
        if attempt >= MAX_COLLECT_RETRIES - 1:
            raise ValueError(error)

        current_cue = (
            cue_text
            + "\n\n【上次输出无效】"
            + error
            + "请从【可选目标】中重新选择，并只输出符合本幕要求的内容。"
        )

    raise ValueError("候选校验失败次数超过上限")


def _candidate_validation_error(
    scene: Scene,
    state: State,
    actor_name: str,
    response: dict,
) -> str:
    """
    返回候选校验错误文本；空字符串表示校验通过。

    支持的结构化字段：
      vote   — Vote
      choose — MutualVote
      target — ChooseTarget / Action
      targets — ChooseMany
    """
    if scene.response_model is None or scene.candidates is None:
        return ""

    data = response.get("data")
    if not isinstance(data, dict):
        return ""

    field_name = _selected_candidate_field(data)
    if field_name is None:
        return ""

    selected = data.get(field_name)
    if field_name == "target" and data.get("action") is False:
        return ""

    candidates = _resolve_scene_candidates(scene, state, actor_name=actor_name)
    if not candidates:
        return ""

    if field_name == "targets":
        if not isinstance(selected, list):
            return f"{scene.name} 的字段 targets 必须是列表。"
        constraint_error = _candidate_constraint_error(scene, selected, candidates, state)
        if constraint_error:
            return constraint_error
        invalid = [item for item in selected if item not in candidates]
        if not invalid:
            return ""
        candidate_text = "、".join(str(name) for name in candidates)
        return (
            f"{scene.name} 的字段 targets 包含非法候选 {invalid!r}；"
            f"当前可选目标：{candidate_text}。"
        )

    if selected in candidates:
        return ""

    candidate_text = "、".join(str(name) for name in candidates)
    return (
        f"{scene.name} 的字段 {field_name}={selected!r} 不在候选集中；"
        f"当前可选目标：{candidate_text}。"
    )


def _candidate_constraint_error(scene: Scene, selected: list, candidates: list, state: State | None = None) -> str:
    """校验 ChooseMany 的数量和去重约束。

    支持静态数字，也支持 `{state: GAME.current_team_size}` 这类运行时约束。
    Support both static numbers and runtime constraints such as
    `{state: GAME.current_team_size}`.
    """
    constraints = scene.candidate_constraints or {}

    expected_count = _resolve_candidate_count_constraint(
        constraints.get("count"),
        candidates=candidates,
        state=state,
    )
    if expected_count is not None and len(selected) != expected_count:
        return f"{scene.name} 的字段 targets 必须选择 {expected_count} 个目标。"

    min_count = _resolve_candidate_count_constraint(
        constraints.get("min"),
        candidates=candidates,
        state=state,
    )
    if min_count is not None and len(selected) < min_count:
        return f"{scene.name} 的字段 targets 至少选择 {min_count} 个目标。"

    max_count = _resolve_candidate_count_constraint(
        constraints.get("max"),
        candidates=candidates,
        state=state,
    )
    if max_count is not None and len(selected) > max_count:
        return f"{scene.name} 的字段 targets 至多选择 {max_count} 个目标。"

    if constraints.get("distinct", False) and len(set(selected)) != len(selected):
        return f"{scene.name} 的字段 targets 不能包含重复目标。"

    return ""


def _resolve_candidate_count_constraint(value: Any, candidates: list, state: State | None) -> int | None:
    """解析 ChooseMany 数量约束。"""
    if value is None:
        return None
    if value == "all_candidates":
        return len(candidates)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    if isinstance(value, dict) and state is not None:
        if "state" in value:
            path = str(value["state"])
            if "." in path:
                entity, attr = path.split(".", 1)
                resolved = state.get_attr(entity, attr)
                if resolved is not None:
                    return int(resolved)
    return None


def _selected_candidate_field(data: dict) -> str | None:
    """识别结构化动作中代表候选目标的字段名。"""
    for field_name in ("vote", "choose", "target", "targets"):
        if field_name in data:
            return field_name
    return None
