"""消息渲染测试。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from drama_engine.core.engine import Scene, Simultaneous, State, Vocabulary, _default_response_text, _render_scene_messages


def _message_state():
    """构造消息渲染测试需要的最小 State。"""
    vocab = Vocabulary(
        roles=frozenset(),
        factions=frozenset(),
        scopes=frozenset({"town"}),
        abilities=frozenset(),
    )
    state = State(vocab)
    state.register_entity("GAME", {})
    state.register_entity("Player_1", {"alive": False, "role": "hunter"})
    state.register_entity("Player_2", {"alive": True})
    return state


def test_confirm_audience_text_omits_reason_and_target():
    """Action 只展示是否确认，不应输出目标或理由。"""
    scene = Scene(
        name="sheriff-join",
        display_name="上警",
        scope="town",
        dialogue_policy=Simultaneous(),
        participants=lambda state: set(),
        cue="是否上警？",
        response_model=object(),
    )
    response = {
        "actor": "Player_12",
        "data": {
            "action": True,
        },
    }

    text = _default_response_text(scene, response, "audience")

    assert "未指定目标" not in text
    assert "理由" not in text
    assert text == "【Player_12｜上警】我选择上警。"


def test_sheriff_join_false_uses_not_join_text():
    """不上警应明确显示为“我选择不上警”，而不是“该玩家选择否”。"""
    scene = Scene(
        name="sheriff-join",
        display_name="上警",
        scope="town",
        dialogue_policy=Simultaneous(),
        participants=lambda state: set(),
        cue="是否上警？",
        response_model=object(),
    )
    response = {
        "actor": "Player_3",
        "data": {
            "action": False,
        },
    }

    text = _default_response_text(scene, response, "scope")

    assert text == "【Player_3｜上警】我选择不上警。"


def test_maybe_act_without_target_omits_unspecified_target_text():
    """Action 无目标时，默认文案不应输出“目标是 未指定目标”。"""
    scene = Scene(
        name="sheriff-join",
        display_name="上警",
        scope="town",
        dialogue_policy=Simultaneous(),
        participants=lambda state: set(),
        cue="是否上警？",
        response_model=object(),
    )
    response = {
        "actor": "Player_6",
        "data": {
            "action": True,
            "target": None,
            "reason": "上警争取警徽",
        },
    }

    text = _default_response_text(scene, response, "self")

    assert "未指定目标" not in text
    assert text == "【Player_6｜上警】我选择上警。理由：上警争取警徽"


def test_maybe_act_with_target_keeps_target_text():
    """Action 有目标时，默认文案仍应输出目标。"""
    scene = Scene(
        name="hunter-day",
        display_name="猎人开枪",
        scope="town",
        dialogue_policy=Simultaneous(),
        participants=lambda state: set(),
        cue="是否开枪？",
        response_model=object(),
    )
    response = {
        "actor": "Player_1",
        "data": {
            "action": True,
            "target": "Player_2",
            "reason": "带走嫌疑人",
        },
    }

    text = _default_response_text(scene, response, "self")

    assert text == "【Player_1｜猎人开枪】我选择行动，目标是 Player_2。理由：带走嫌疑人"


def test_hunter_no_shot_stays_private():
    """猎人不开枪时，不应向 town 公开猎人身份或不开枪选择。"""
    scene = Scene(
        name="hunter-day",
        display_name="猎人开枪（白天）",
        scope="town",
        dialogue_policy=Simultaneous(),
        participants=lambda state: set(),
        cue="你已死亡。作为猎人，你可以选择开枪或不开枪。是否开枪？",
        response_model=object(),
        announce_response_cue=False,
        response_messages=[{
            "source": "action",
            "targets": [
                {
                    "to": "self",
                    "render": "【{actor}｜{scene.display_name}】我选择{data.action|true:开枪,false:不开枪}，目标：{data.target|none:无}。理由：{data.reason}",
                },
                {
                    "to": "scope",
                    "when": {
                        "all": [
                            {"value": {"ref": "data.action"}, "equal": True},
                            {"value": {"ref": "data.target"}, "not_null": True},
                        ]
                    },
                    "render": "{actor} 是猎人，开枪带走了 {data.target}。",
                },
            ],
        }],
    )
    response = {
        "actor": "Player_1",
        "data": {"action": False, "target": None, "reason": "保留信息"},
    }

    routed = _render_scene_messages(scene, _message_state(), response)

    assert "scope" not in routed
    assert routed["self"] == "【Player_1｜猎人开枪（白天）】我选择不开枪，目标：无。理由：保留信息"


def test_hunter_shot_publicly_announces_only_result():
    """猎人开枪时，town 只看到主持人式公开结果。"""
    scene = Scene(
        name="hunter-day",
        display_name="猎人开枪（白天）",
        scope="town",
        dialogue_policy=Simultaneous(),
        participants=lambda state: set(),
        cue="你已死亡。作为猎人，你可以选择开枪或不开枪。是否开枪？",
        response_model=object(),
        announce_response_cue=False,
        response_messages=[{
            "source": "action",
            "targets": [
                {"to": "self", "render": "self"},
                {
                    "to": "scope",
                    "when": {
                        "all": [
                            {"value": {"ref": "data.action"}, "equal": True},
                            {"value": {"ref": "data.target"}, "not_null": True},
                        ]
                    },
                    "render": "{actor} 是猎人，开枪带走了 {data.target}。",
                },
            ],
        }],
    )
    response = {
        "actor": "Player_1",
        "data": {"action": True, "target": "Player_2", "reason": "狼面大"},
    }

    routed = _render_scene_messages(scene, _message_state(), response)

    assert routed["scope"] == "Player_1 是猎人，开枪带走了 Player_2。"
    assert "理由" not in routed["scope"]
