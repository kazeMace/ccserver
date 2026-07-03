"""interactive_session runtime tests."""

from types import SimpleNamespace

import pytest
import yaml

from drama_engine.core.dsl.components import CandidateResolver, EffectExecutor, ValueResolver
from drama_engine.core.dsl.components.conditions import ConditionEvaluator
from drama_engine.core.dsl.plugins import build_default_plugin_registry
from drama_engine.core.engine import Cast, SetAttr, State, StateWriter, Vocabulary
from drama_engine.core.ports.input import InputBridge
from drama_engine.core.ports.memory import RuntimeMemoryStore
from drama_engine.core.runner.config import RuntimeConfigParser
from drama_engine.core.runner.dispatch import build_runner_for_session, read_runtime_declaration
from drama_engine.core.runtime.interactive_session.actions.participant import ParticipantActionExecutor
from drama_engine.core.runtime.interactive_session import InteractiveSessionRunner
from drama_engine.core.runtime.interactive_session.compiler import InteractiveSessionCompiler
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.models import ParticipantActionSpec, ScopeSpec
from drama_engine.core.runtime.interactive_session.patch.journal import PatchJournal
from drama_engine.core.session.lifecycle import RuntimeState
from drama_engine.core.session.ports import ServicePorts
from drama_engine.core.session.summary import SummaryProvider


def _runtime_for(script_path: str):
    """Build a minimal runtime container for runner tests."""
    session = _FakeSession(script_path)
    event_store = _FakeEventStore()
    action_service = _FakeActionService()
    return SimpleNamespace(
        session=session,
        service=ServicePorts(
            session_state=session,
            event_sink=event_store,
            action_view=action_service,
        ),
        event_store=event_store,
        action_service=action_service,
        runtime_state=RuntimeState(),
        input_bridge=InputBridge(),
        runtime_config_parser=RuntimeConfigParser(),
        memory_store=RuntimeMemoryStore(),
        summary_provider=SummaryProvider(),
        step_gate=None,
    )


class _FakeSession:
    """Minimal session object."""

    def __init__(self, script_path: str) -> None:
        self.script_path = script_path
        self.params = {}
        self.metadata = {}
        self.status = "lobby"
        self.seat_ids = ["A", "B", "C", "D"]
        self.human_seat_ids = set()
        self.session_id = "interactive-test"

    def set_status(self, status: str) -> None:
        """Set status."""
        self.status = status


class _FakeEventStore:
    """Collect emitted events."""

    def __init__(self) -> None:
        self.public = []
        self.host = []

    def append_public(self, event: dict) -> None:
        """Append public event."""
        self.public.append(event)

    def append_host(self, event: dict) -> None:
        """Append host event."""
        self.host.append(event)

    def append_private(self, seat_id: str, event: dict) -> None:
        """Append private event."""
        self.host.append({"seat_id": seat_id, **event})


class _FakeActionService:
    """Minimal action service."""

    session_id = "interactive-test"


class _ScriptedActor:
    """Test actor that returns scripted responses."""

    def __init__(self, name: str, responses: list[dict]) -> None:
        self.name = name
        self.responses = list(responses)
        self.candidates = []

    def set_candidates(self, candidates: list) -> None:
        """Record candidates."""
        self.candidates = list(candidates)

    def set_scene_context(self, scene_id: str, scene_name: str) -> None:
        """Accept scene context."""

    async def perceive(self, event: dict) -> None:
        """Ignore perceived events."""

    async def act(self, cue: str, collect=None) -> dict:
        """Return next scripted response."""
        return self.responses.pop(0)


def test_interactive_session_declaration_and_dispatch():
    """runtime.type=interactive_session should dispatch to the new runner."""
    script_path = "drama_engine/scripts/interactive_session/story/text_adventure_interactive.yaml"

    declaration = read_runtime_declaration(script_path)
    runner = build_runner_for_session(_runtime_for(script_path), dry_run=True)

    assert declaration.type == "interactive_session"
    assert isinstance(runner, InteractiveSessionRunner)


def test_interactive_session_compiler_compiles_new_scripts():
    """Compiler should parse all new-syntax interactive scripts."""
    compiler = InteractiveSessionCompiler()

    story = compiler.compile("drama_engine/scripts/interactive_session/story/text_adventure_interactive.yaml")
    discussion = compiler.compile("drama_engine/scripts/interactive_session/deduction/dynamic_schedule_discussion.yaml")

    assert story.flow.type == "sequence"
    assert list(story.scenes) == ["intro", "first_choice"]
    assert discussion.flow.type == "state_machine"
    assert discussion.scenes["day_discussion"].schedule.dynamic.enabled is True


def test_builtin_condition_supports_count_ref_where():
    """New canonical condition syntax should support count.ref/where."""
    vocab = Vocabulary(
        roles=frozenset(),
        factions=frozenset(),
        scopes=frozenset(),
        abilities=frozenset(),
    )
    state = State(vocab)
    state.register_entity("GAME", {"players": ["A", "B", "C"]})
    for name in ["A", "B", "C"]:
        state.register_entity(name, {"alive": True})
    StateWriter(state).apply(SetAttr("C", "alive", False))

    passed = ConditionEvaluator().evaluate(
        {
            "evaluator": "builtin",
            "condition": {
                "left": {
                    "count": {
                        "ref": "GAME.players",
                        "where": {
                            "left": "alive",
                            "op": "equal",
                            "right": True,
                        },
                    }
                },
                "op": "equal",
                "right": 2,
            },
        },
        state,
        actor=None,
    )

    assert passed is True


@pytest.mark.asyncio
async def test_interactive_session_runner_can_assign_start_and_end():
    """Runner should execute the dry-run dynamic schedule sample."""
    script_path = "drama_engine/scripts/interactive_session/deduction/dynamic_schedule_discussion.yaml"
    runtime = _runtime_for(script_path)
    runner = build_runner_for_session(runtime, dry_run=True)

    await runner.assign()
    assert runtime.session.status == "assigned"
    assert runner.context.actor_runtime.cast is not None
    assert set(runner.context.actor_runtime.cast.all_names()) == {"A", "B", "C", "D"}

    await runner.start()
    await runtime.runtime_state.task

    assert runtime.session.status == "ended"
    assert runtime.session.metadata["interactive_session"]["result"] in {
        "vote_completed",
        "interactive_session_completed",
    }
    assert any(event["kind"] == "interactive_message" for event in runtime.event_store.public)
    assert any(
        patch["patch_type"] == "schedule_patch"
        for patch in runtime.session.metadata["interactive_session"]["patches"]
    )
    assert any(event["kind"] == "interactive_schedule_merge" for event in runtime.event_store.host)


@pytest.mark.asyncio
async def test_controller_choice_target_executes_even_when_sequence_scene_is_last(tmp_path):
    """A choice target should execute immediately, even from the final sequence scene."""
    script_path = tmp_path / "choice_jump.yaml"
    script_path.write_text(yaml.safe_dump({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["P1"]},
        "flow": {"type": "sequence", "scenes": ["choice"]},
        "scenes": {
            "choice": {
                "scope": {"id": "story", "visibility": "public"},
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
                "controller_action": {
                    "enabled": True,
                    "controller": {"type": "system"},
                    "kind": "choice",
                    "choices": [{"id": "go", "text": "go", "to": "ending"}],
                },
            },
            "ending": {
                "scope": {"id": "story", "visibility": "public"},
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
                "resolution": {"effects": [{"type": "set_state", "path": "GAME.hit", "value": True}]},
            },
        },
        "referee": {
            "enabled": True,
            "check_on": "after_scene",
            "rules": [
                {
                    "when": {"left": "GAME.hit", "op": "equal", "right": True},
                    "result": {"end": "hit_ending"},
                }
            ],
        },
    }, allow_unicode=True), encoding="utf-8")
    runtime = _runtime_for(str(script_path))
    runner = build_runner_for_session(runtime, dry_run=True)

    await runner.assign()
    await runner.start()
    await runtime.runtime_state.task

    assert runtime.session.metadata["interactive_session"]["result"] == "hit_ending"


@pytest.mark.asyncio
async def test_grow_flow_patch_is_materialized_and_executed(tmp_path):
    """grow_flow should add the generated node to the live flow."""
    script_path = tmp_path / "grow_flow.yaml"
    generated_scene = {
        "id": "generated_scene",
        "scope": {"id": "story", "visibility": "public"},
        "participants": {"static": []},
        "schedule": {"mode": "none"},
        "participant_action": {"kind": "none", "response": {"mode": "none"}},
        "resolution": {"effects": [{"type": "set_state", "path": "GAME.generated", "value": True}]},
    }
    script_path.write_text(yaml.safe_dump({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["P1"]},
        "flow": {"type": "sequence", "scenes": ["start"]},
        "scenes": {
            "start": {
                "scope": {"id": "story", "visibility": "public"},
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
                "controller_action": {
                    "enabled": True,
                    "controller": {"type": "system"},
                    "kind": "free_text",
                    "free_input": {
                        "enabled": True,
                        "mode": "grow_flow",
                        "patch": {"type": "add_scene", "after": "start", "scene": generated_scene},
                    },
                },
            },
        },
        "referee": {
            "enabled": True,
            "check_on": "after_scene",
            "rules": [
                {
                    "when": {"left": "GAME.generated", "op": "equal", "right": True},
                    "result": {"end": "generated_done"},
                }
            ],
        },
    }, allow_unicode=True), encoding="utf-8")
    runtime = _runtime_for(str(script_path))
    runner = build_runner_for_session(runtime, dry_run=True)

    await runner.assign()
    await runner.start()
    await runtime.runtime_state.task

    assert runtime.session.metadata["interactive_session"]["result"] == "generated_done"
    assert any(
        record["patch_type"] == "flow_patch"
        for record in runtime.session.metadata["interactive_session"]["patches"]
    )


@pytest.mark.asyncio
async def test_participant_action_rejects_invalid_candidate_then_retries():
    """Structured action output must be inside resolved candidates."""
    compiler = InteractiveSessionCompiler()
    script = compiler.compile_doc({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["A", "B"]},
        "flow": {"type": "sequence", "scenes": ["vote"]},
        "scenes": {"vote": {"participants": {"static": ["A"]}}},
    })
    vocab = Vocabulary(roles=frozenset(), factions=frozenset(), scopes=frozenset(), abilities=frozenset())
    state = State(vocab)
    state.register_entity("GAME", {"players": ["A", "B"]})
    state.register_entity("A", {"alive": True})
    state.register_entity("B", {"alive": True})
    plugins = build_default_plugin_registry()
    evaluator = ConditionEvaluator(plugins)
    actor = _ScriptedActor("A", [
        {"actor": "A", "text": "bad", "data": {"vote": "Z", "reason": "bad"}},
        {"actor": "A", "text": "good", "data": {"vote": "B", "reason": "good"}},
    ])
    cast = Cast()
    cast.add(actor)
    ctx = InteractiveExecutionContext(
        script=script,
        state=state,
        writer=StateWriter(state),
        cast=cast,
        condition_evaluator=evaluator,
        effect_executor=EffectExecutor(evaluator, plugins),
        candidate_resolver=CandidateResolver(evaluator),
        value_resolver=ValueResolver(plugins),
        plugin_registry=plugins,
        patch_journal=PatchJournal(),
        emit_public=lambda event: None,
        emit_host=lambda event: None,
        session_metadata={},
        current_scene_id="vote",
    )

    response = await ParticipantActionExecutor().collect_one(
        ctx,
        "A",
        ParticipantActionSpec(
            kind="vote",
            target="required",
            candidates={"filter": {"source": "GAME.players"}},
            response={"mode": "structured", "schema": "vote"},
        ),
        ScopeSpec(id="public", visibility="public"),
        ["A"],
    )

    assert response["data"]["vote"] == "B"
    assert actor.candidates == ["A", "B"]


@pytest.mark.asyncio
async def test_referee_result_can_apply_effects_and_jump(tmp_path):
    """Referee result should support effects and non-terminal jumps."""
    script_path = tmp_path / "referee_jump.yaml"
    script_path.write_text(yaml.safe_dump({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["P1"]},
        "flow": {"type": "sequence", "scenes": ["start"]},
        "scenes": {
            "start": {
                "scope": {"id": "story", "visibility": "public"},
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
                "referee": {
                    "enabled": True,
                    "check_on": "after_scene",
                    "rules": [
                        {
                            "when": {"left": "GAME.round", "op": "equal", "right": 1},
                            "result": {
                                "jump": "ending",
                                "effects": [{"type": "set_state", "path": "GAME.referee_effect", "value": True}],
                            },
                        }
                    ],
                },
            },
            "ending": {
                "scope": {"id": "story", "visibility": "public"},
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
                "resolution": {"effects": [{"type": "set_state", "path": "GAME.ending_seen", "value": True}]},
            },
        },
        "referee": {
            "enabled": True,
            "check_on": "after_scene",
            "rules": [
                {
                    "when": {"left": "GAME.ending_seen", "op": "equal", "right": True},
                    "result": {"end": "jumped"},
                }
            ],
        },
    }, allow_unicode=True), encoding="utf-8")
    runtime = _runtime_for(str(script_path))
    runner = build_runner_for_session(runtime, dry_run=True)

    await runner.assign()
    await runner.start()
    await runtime.runtime_state.task

    assert runtime.session.metadata["interactive_session"]["result"] == "jumped"


@pytest.mark.asyncio
async def test_script_declared_runtime_service_maps_choice(tmp_path):
    """Script plugins should register runtime services used by free_input."""
    script_path = tmp_path / "plugin_mapper.yaml"
    script_path.write_text(yaml.safe_dump({
        "runtime": {"type": "interactive_session"},
        "plugins": [
            {
                "runtime_services": {
                    "map_free_text_to_choice": {"result": {"selected_choice": "leave"}}
                }
            }
        ],
        "players": {"ids": ["P1"]},
        "flow": {"type": "sequence", "scenes": ["choice"]},
        "scenes": {
            "choice": {
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
                "controller_action": {
                    "enabled": True,
                    "controller": {"type": "system"},
                    "kind": "choice",
                    "choices": [
                        {"id": "stay", "text": "stay", "to": "stay"},
                        {"id": "leave", "text": "leave", "to": "leave"},
                    ],
                    "free_input": {
                        "enabled": True,
                        "mode": "choose_mapping",
                        "mapper": {"evaluator": "plugin", "name": "map_free_text_to_choice"},
                    },
                },
            },
            "stay": {
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
                "resolution": {"effects": [{"type": "set_state", "path": "GAME.result", "value": "stay"}]},
            },
            "leave": {
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
                "resolution": {"effects": [{"type": "set_state", "path": "GAME.result", "value": "leave"}]},
            },
        },
        "referee": {
            "enabled": True,
            "check_on": "after_scene",
            "rules": [
                {"when": {"left": "GAME.result", "op": "equal", "right": "leave"}, "result": {"end": "leave_done"}}
            ],
        },
    }, allow_unicode=True), encoding="utf-8")
    runtime = _runtime_for(str(script_path))
    runner = build_runner_for_session(runtime, dry_run=True)

    await runner.assign()
    await runner.start()
    await runtime.runtime_state.task

    assert runtime.session.metadata["interactive_session"]["result"] == "leave_done"


@pytest.mark.asyncio
async def test_referee_evaluator_uses_result_effects(tmp_path):
    """Direct referee evaluator should apply its configured result."""
    script_path = tmp_path / "referee_evaluator.yaml"
    script_path.write_text(yaml.safe_dump({
        "runtime": {"type": "interactive_session"},
        "plugins": [{"conditions": {"always_end": {"result": True}}}],
        "players": {"ids": ["P1"]},
        "flow": {"type": "sequence", "scenes": ["start"]},
        "scenes": {
            "start": {
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
            }
        },
        "referee": {
            "enabled": True,
            "check_on": "after_scene",
            "evaluator": "plugin",
            "name": "always_end",
            "result": {
                "effects": [{"type": "set_state", "path": "GAME.evaluator_effect", "value": True}],
                "end": "plugin_end",
            },
        },
    }, allow_unicode=True), encoding="utf-8")
    runtime = _runtime_for(str(script_path))
    runner = build_runner_for_session(runtime, dry_run=True)

    await runner.assign()
    await runner.start()
    await runtime.runtime_state.task

    assert runtime.session.metadata["interactive_session"]["result"] == "plugin_end"


@pytest.mark.asyncio
async def test_branch_then_return_executes_branch_and_return_target(tmp_path):
    """branch_then_return should execute generated branch then return to target."""
    script_path = tmp_path / "branch_return.yaml"
    script_path.write_text(yaml.safe_dump({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["P1"]},
        "flow": {"type": "sequence", "scenes": ["start"]},
        "scenes": {
            "start": {
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
                "controller_action": {
                    "enabled": True,
                    "controller": {"type": "system"},
                    "kind": "free_text",
                    "free_input": {
                        "enabled": True,
                        "mode": "branch_then_return",
                        "return_to": {"type": "scene", "id": "main"},
                    },
                },
            },
            "main": {
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
                "resolution": {"effects": [{"type": "set_state", "path": "GAME.returned", "value": True}]},
            },
        },
        "referee": {
            "enabled": True,
            "check_on": "after_scene",
            "rules": [
                {"when": {"left": "GAME.returned", "op": "equal", "right": True}, "result": {"end": "returned"}}
            ],
        },
    }, allow_unicode=True), encoding="utf-8")
    runtime = _runtime_for(str(script_path))
    runner = build_runner_for_session(runtime, dry_run=True)

    await runner.assign()
    await runner.start()
    await runtime.runtime_state.task

    assert runtime.session.metadata["interactive_session"]["result"] == "returned"
    patches = runtime.session.metadata["interactive_session"]["patches"]
    assert any(item["patch_type"] == "branch_patch" for item in patches)


@pytest.mark.asyncio
async def test_message_alias_hook_and_generated_beat_referee(tmp_path):
    """MESSAGE alias and generated beat checks should be usable in DSL."""
    script_path = tmp_path / "message_alias.yaml"
    script_path.write_text(yaml.safe_dump({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["A"]},
        "flow": {"type": "sequence", "scenes": ["talk", "generate"]},
        "scenes": {
            "talk": {
                "participants": {"static": ["A"]},
                "schedule": {"mode": "single", "actor": "A"},
                "participant_action": {"kind": "speak", "response": {"mode": "text"}},
                "hooks": {
                    "on_message": [
                        {
                            "when": {"left": "MESSAGE.text", "op": "contains", "right": "dry-run"},
                            "do": [{"type": "set_state", "path": "GAME.heard", "value": True}],
                        }
                    ]
                },
            },
            "generate": {
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
                "controller_action": {
                    "enabled": True,
                    "controller": {"type": "system"},
                    "kind": "free_text",
                    "free_input": {
                        "enabled": True,
                        "mode": "free_continue",
                        "max_beats": 2,
                        "generator": {"text": "剧情继续"},
                    },
                },
            },
        },
        "referee": {
            "enabled": True,
            "check_on": ["after_scene", "after_generated_beat"],
            "rules": [
                {"when": {"left": "GAME.heard", "op": "equal", "right": True}, "result": {"jump": "generate"}},
                {"when": {"left": "MESSAGE.text", "op": "contains", "right": "剧情继续"}, "result": {"end": "beat_end"}},
            ],
        },
    }, allow_unicode=True), encoding="utf-8")
    runtime = _runtime_for(str(script_path))
    runner = build_runner_for_session(runtime, dry_run=True)

    await runner.assign()
    await runner.start()
    await runtime.runtime_state.task

    assert runtime.session.metadata["interactive_session"]["result"] == "beat_end"


@pytest.mark.asyncio
async def test_dynamic_schedule_respects_after_round_check_on(tmp_path):
    """dynamic.check_on=after_round should trigger once after the round."""
    script_path = tmp_path / "dynamic_after_round.yaml"
    script_path.write_text(yaml.safe_dump({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["A", "B"]},
        "flow": {"type": "sequence", "scenes": ["talk"]},
        "scenes": {
            "talk": {
                "participants": {"static": ["A", "B"]},
                "schedule": {
                    "mode": "sequential",
                    "dynamic": {
                        "enabled": True,
                        "check_on": "after_round",
                        "detector": {
                            "patch": {
                                "type": "push_schedule",
                                "mode": "single",
                                "participants": ["A"],
                                "max_turns": 1,
                            }
                        },
                    },
                },
                "participant_action": {"kind": "speak", "response": {"mode": "text"}},
            }
        },
    }, allow_unicode=True), encoding="utf-8")
    runtime = _runtime_for(str(script_path))
    runner = build_runner_for_session(runtime, dry_run=True)

    await runner.assign()
    await runner.start()
    await runtime.runtime_state.task

    patches = runtime.session.metadata["interactive_session"]["patches"]
    pushed = [
        item for item in patches
        if item["patch_type"] == "schedule_patch" and item["payload"].get("type") == "push_schedule"
    ]
    assert len(pushed) == 1
