"""StateMachineFlow 流程测试。"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from drama_engine.core.engine import Scene, SetAttr, State, StateMachineFlow, StateWriter, Vocabulary


def _state() -> State:
    vocab = Vocabulary(
        roles=frozenset(),
        factions=frozenset(),
        scopes=frozenset(),
        abilities=frozenset(),
    )
    state = State(vocab)
    state.register_entity("GAME", {"go_pk": False, "entered": 0, "exited": 0})
    return state


def _scene(name: str) -> Scene:
    return Scene(
        name=name,
        scope="public",
        participants=lambda state: set(),
        cue="",
        dialogue_policy=None,
    )


def test_state_machine_flow_starts_at_initial_state():
    """首次 next_scenes 应返回 initial 节点的 scenes。"""
    flow = StateMachineFlow(
        initial="night",
        states={
            "night": {"scenes": [_scene("wolf-vote")], "transitions": [{"to": "day"}]},
            "day": {"scenes": [_scene("day-vote")], "transitions": [{"to": "night"}]},
        },
    )

    scenes = flow.next_scenes(_state())

    assert [scene.name for scene in scenes] == ["wolf-vote"]
    assert flow.current == "night"


def test_state_machine_flow_uses_first_matching_transition():
    """后续 next_scenes 应按 transitions 的第一条满足条件切换节点。"""
    state = _state()
    flow = StateMachineFlow(
        initial="day",
        states={
            "day": {
                "scenes": [_scene("day-vote")],
                "transitions": [
                    {"to": "pk", "when": lambda state: state.get_attr("GAME", "go_pk")},
                    {"to": "night"},
                ],
            },
            "pk": {"scenes": [_scene("pk-vote")], "transitions": [{"to": "night"}]},
            "night": {"scenes": [_scene("night-fall")], "transitions": [{"to": "day"}]},
        },
    )

    writer = StateWriter(state)
    assert [scene.name for scene in flow.next_scenes(state)] == ["day-vote"]
    flow.after_batch(state, writer)
    assert [scene.name for scene in flow.next_scenes(state)] == ["night-fall"]

    flow = StateMachineFlow(
        initial="day",
        states={
            "day": {
                "scenes": [_scene("day-vote")],
                "transitions": [
                    {"to": "pk", "when": lambda state: state.get_attr("GAME", "go_pk")},
                    {"to": "night"},
                ],
            },
            "pk": {"scenes": [_scene("pk-vote")], "transitions": [{"to": "night"}]},
            "night": {"scenes": [_scene("night-fall")], "transitions": [{"to": "day"}]},
        },
    )
    state = _state()
    writer = StateWriter(state)
    writer.apply(SetAttr("GAME", "go_pk", True))

    assert [scene.name for scene in flow.next_scenes(state)] == ["day-vote"]
    flow.after_batch(state, writer)
    assert [scene.name for scene in flow.next_scenes(state)] == ["pk-vote"]


def test_state_machine_flow_terminal_state_stops_looping():
    """进入 terminal 节点时返回空 scenes，并把 loop 置为 False。"""
    flow = StateMachineFlow(
        initial="day",
        states={
            "day": {"scenes": [_scene("day-vote")], "transitions": [{"to": "end"}]},
            "end": {"terminal": True, "scenes": [], "transitions": []},
        },
    )

    state = _state()
    writer = StateWriter(state)
    assert [scene.name for scene in flow.next_scenes(state)] == ["day-vote"]
    flow.after_batch(state, writer)
    assert flow.next_scenes(state) == []
    assert flow.loop is False


def test_state_machine_flow_runs_entry_and_exit_actions_once_per_state_entry():
    """状态机应在进入阶段时执行 entry，在离开阶段时执行 exit。"""
    state = _state()
    writer = StateWriter(state)

    def entry(state, writer):
        value = state.get_attr("GAME", "entered") or 0
        writer.apply(SetAttr("GAME", "entered", value + 1))

    def exit_action(state, writer):
        value = state.get_attr("GAME", "exited") or 0
        writer.apply(SetAttr("GAME", "exited", value + 1))

    flow = StateMachineFlow(
        initial="day",
        states={
            "day": {
                "entry": entry,
                "exit": exit_action,
                "scenes": [_scene("day-vote")],
                "transitions": [{"to": "night"}],
            },
            "night": {"scenes": [_scene("night-fall")], "transitions": [{"to": "day"}]},
        },
    )

    flow.next_scenes(state)
    flow.on_batch_start(state, writer)
    flow.on_batch_start(state, writer)
    assert state.get_attr("GAME", "entered") == 1

    flow.after_batch(state, writer)
    assert state.get_attr("GAME", "exited") == 1
    assert flow.current == "night"


def test_state_machine_flow_set_next_interrupts_current_batch():
    """GAME.__flow_next_state 存在时，after_scene 应切换阶段并要求跳过剩余 scenes。"""
    state = _state()
    writer = StateWriter(state)
    flow = StateMachineFlow(
        initial="day",
        states={
            "day": {
                "scenes": [_scene("boom"), _scene("day-vote")],
                "transitions": [{"to": "night"}],
            },
            "night": {"scenes": [_scene("night-fall")], "transitions": [{"to": "day"}]},
        },
    )

    scenes = flow.next_scenes(state)
    writer.apply(SetAttr("GAME", "__flow_next_state", "night"))
    should_continue = flow.after_scene(scenes[0], state, writer)

    assert should_continue is False
    assert flow.current == "night"
    assert state.get_attr("GAME", "__flow_next_state") is None
    flow.after_batch(state, writer)
    assert [scene.name for scene in flow.next_scenes(state)] == ["night-fall"]
