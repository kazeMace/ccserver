"""interactive_session runtime tests."""

import asyncio
from types import SimpleNamespace
import urllib.error
import urllib.request

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
from drama_engine.core.runtime.interactive_session.actions.free_input import FreeInputExecutor
from drama_engine.core.runtime.interactive_session.actions.controller import ControllerActionExecutor
from drama_engine.core.runtime.interactive_session import InteractiveSessionRunner
from drama_engine.core.runtime.interactive_session.compiler import InteractiveSessionCompiler
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.flow.executor import FlowExecutor
from drama_engine.core.runtime.interactive_session.models import ControllerActionSpec, ParticipantActionSpec, ScopeSpec
from drama_engine.core.runtime.interactive_session.patch.journal import PatchJournal
from drama_engine.core.runtime.interactive_session.scene.executor import SceneExecutor
from drama_engine.core.runtime.interactive_session.services.runtime_services import RuntimeServiceCaller
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


class _AsyncInsideAgent:
    """Test ccserver-like Agent with async run()."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.prompts = []

    async def run(self, prompt: str) -> str:
        """Return scripted text."""
        self.prompts.append(prompt)
        return self.text


def _interactive_ctx(script_doc: dict, actors: list[_ScriptedActor] | None = None) -> InteractiveExecutionContext:
    """Build a direct interactive_session execution context for focused tests."""
    compiler = InteractiveSessionCompiler()
    script = compiler.compile_doc(script_doc)
    players = list((script_doc.get("players") or {}).get("ids") or ["P1"])
    vocab = Vocabulary(roles=frozenset(), factions=frozenset(), scopes=frozenset(), abilities=frozenset())
    state = State(vocab)
    state.register_entity("GAME", {"players": players, "round": 1})
    state.register_entity("SCENE", {})
    state.register_entity("STORY", {})
    for name in players:
        state.register_entity(name, {"alive": True})
    plugins = build_default_plugin_registry()
    evaluator = ConditionEvaluator(plugins)
    cast = Cast()
    for actor in actors or []:
        cast.add(actor)
    return InteractiveExecutionContext(
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
        current_scene_id=list(script.scenes)[0],
        base_raw=script.raw,
    )


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


@pytest.mark.asyncio
async def test_flow_patch_failure_does_not_pollute_journal():
    """Invalid flow_patch should fail before journal append."""
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["P1"]},
        "flow": {"type": "sequence", "scenes": ["start"]},
        "scenes": {
            "start": {
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
            }
        },
    })

    with pytest.raises(ValueError):
        await FreeInputExecutor().execute(
            ctx,
            "grow_flow",
            {"patch": {"type": "add_scene", "scene": {}}},
            {"actor": "system", "text": "bad"},
        )

    assert ctx.patch_journal.snapshot() == []
    assert list(ctx.script.scenes) == ["start"]


@pytest.mark.asyncio
async def test_set_state_flow_patch_requires_target_before_journal():
    """Invalid set_state flow_patch should fail before journal append."""
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["P1"]},
        "flow": {"type": "sequence", "scenes": ["start"]},
        "scenes": {
            "start": {
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
            }
        },
    })

    with pytest.raises(ValueError):
        await FreeInputExecutor().execute(
            ctx,
            "grow_flow",
            {"patch": {"type": "set_state", "value": True}},
            {"actor": "system", "text": "bad"},
        )

    assert ctx.patch_journal.snapshot() == []
    assert ctx.state.get_attr("GAME", "missing_target") is None


@pytest.mark.asyncio
async def test_branch_then_return_rejects_non_scene_patch_before_journal():
    """branch_then_return must not journal a non-scene patch."""
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["P1"]},
        "flow": {"type": "sequence", "scenes": ["start"]},
        "scenes": {
            "start": {
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
            }
        },
    })

    with pytest.raises(ValueError):
        await FreeInputExecutor().execute(
            ctx,
            "branch_then_return",
            {"patch": {"type": "set_state", "path": "GAME.bad_branch", "value": True}},
            {"actor": "system", "text": "bad"},
        )

    assert ctx.patch_journal.snapshot() == []
    assert ctx.state.get_attr("GAME", "bad_branch") is None


@pytest.mark.asyncio
async def test_materialized_flow_uses_immutable_base_flow(tmp_path):
    """Summary should expose base_flow separately from materialized flow."""
    script_path = tmp_path / "immutable_base.yaml"
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
                        "mode": "grow_flow",
                        "patch": {
                            "type": "add_scene",
                            "after": "start",
                            "scene": {
                                "id": "generated_once",
                                "participants": {"static": []},
                                "schedule": {"mode": "none"},
                                "participant_action": {"kind": "none", "response": {"mode": "none"}},
                            },
                        },
                    },
                },
            }
        },
    }, allow_unicode=True), encoding="utf-8")
    runtime = _runtime_for(str(script_path))
    runner = build_runner_for_session(runtime, dry_run=True)

    await runner.assign()
    await runner.start()
    await runtime.runtime_state.task

    summary = runner.summary("host")
    interactive = summary["interactive_session"]
    assert interactive["base_flow"]["flow"]["scenes"] == ["start"]
    assert interactive["materialized_flow"]["flow"]["scenes"] == ["start", "generated_once"]
    assert interactive["materialized_flow"]["flow"]["scenes"].count("generated_once") == 1


@pytest.mark.asyncio
async def test_generated_beats_stop_publishing_after_referee_result(tmp_path):
    """after_generated_beat should be checked after each published beat."""
    script_path = tmp_path / "beat_granularity.yaml"
    script_path.write_text(yaml.safe_dump({
        "runtime": {"type": "interactive_session"},
        "plugins": [
            {
                "runtime_services": {
                    "two_beats": {
                        "result": {
                            "beats": [
                                {"text": "stop here"},
                                {"text": "should not publish"},
                            ]
                        }
                    }
                }
            }
        ],
        "players": {"ids": ["P1"]},
        "flow": {"type": "sequence", "scenes": ["generate"]},
        "scenes": {
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
                        "generator": {"evaluator": "plugin", "name": "two_beats"},
                    },
                },
            }
        },
        "referee": {
            "enabled": True,
            "check_on": "after_generated_beat",
            "rules": [
                {
                    "when": {"left": "MESSAGE.text", "op": "contains", "right": "stop here"},
                    "result": {"end": "stopped"},
                }
            ],
        },
    }, allow_unicode=True), encoding="utf-8")
    runtime = _runtime_for(str(script_path))
    runner = build_runner_for_session(runtime, dry_run=True)

    await runner.assign()
    await runner.start()
    await runtime.runtime_state.task

    beat_texts = [
        event["text"]
        for event in runtime.event_store.public
        if event.get("kind") == "generated_beat"
    ]
    assert runtime.session.metadata["interactive_session"]["result"] == "stopped"
    assert beat_texts == ["stop here"]


@pytest.mark.asyncio
async def test_plugin_participants_are_resolved(tmp_path):
    """participants.evaluator=plugin should select runtime participants."""
    script_path = tmp_path / "plugin_participants.yaml"
    script_path.write_text(yaml.safe_dump({
        "runtime": {"type": "interactive_session"},
        "plugins": [
            {"runtime_services": {"select_current_scene_participants": {"result": {"participants": ["B"]}}}}
        ],
        "players": {"ids": ["A", "B"]},
        "flow": {"type": "sequence", "scenes": ["talk"]},
        "scenes": {
            "talk": {
                "participants": {"evaluator": "plugin", "name": "select_current_scene_participants"},
                "schedule": {"mode": "sequential"},
                "participant_action": {"kind": "speak", "response": {"mode": "text"}},
            }
        },
    }, allow_unicode=True), encoding="utf-8")
    runtime = _runtime_for(str(script_path))
    runner = build_runner_for_session(runtime, dry_run=True)

    await runner.assign()
    await runner.start()
    await runtime.runtime_state.task

    started = [
        event for event in runtime.event_store.public
        if event.get("kind") == "interactive_scene_started"
    ][0]
    assert started["participants"] == ["B"]


@pytest.mark.asyncio
async def test_participants_plugin_shorthand_is_resolved(tmp_path):
    """participants.plugin should resolve through runtime services."""
    script_path = tmp_path / "plugin_participants_shorthand.yaml"
    script_path.write_text(yaml.safe_dump({
        "runtime": {"type": "interactive_session"},
        "plugins": [
            {"runtime_services": {"select_current_scene_participants": {"result": {"participants": ["A"]}}}}
        ],
        "players": {"ids": ["A", "B"]},
        "flow": {"type": "sequence", "scenes": ["talk"]},
        "scenes": {
            "talk": {
                "participants": {"plugin": "select_current_scene_participants"},
                "schedule": {"mode": "sequential"},
                "participant_action": {"kind": "speak", "response": {"mode": "text"}},
            }
        },
    }, allow_unicode=True), encoding="utf-8")
    runtime = _runtime_for(str(script_path))
    runner = build_runner_for_session(runtime, dry_run=True)

    await runner.assign()
    await runner.start()
    await runtime.runtime_state.task

    started = [
        event for event in runtime.event_store.public
        if event.get("kind") == "interactive_scene_started"
    ][0]
    assert started["participants"] == ["A"]


@pytest.mark.asyncio
async def test_dynamic_merge_back_writes_summary_state(tmp_path):
    """dynamic.merge_back should write the configured state target."""
    script_path = tmp_path / "merge_back.yaml"
    script_path.write_text(yaml.safe_dump({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["A", "B"]},
        "flow": {"type": "sequence", "scenes": ["talk"]},
        "scenes": {
            "talk": {
                "participants": {"static": ["A", "B"]},
                "schedule": {
                    "mode": "single",
                    "actor": "A",
                    "dynamic": {
                        "enabled": True,
                        "check_on": "after_message",
                        "detector": {
                            "patch": {
                                "type": "push_schedule",
                                "mode": "single",
                                "participants": ["B"],
                                "max_turns": 1,
                            }
                        },
                        "merge_back": {"mode": "summary", "to": "SCENE.dynamic_schedule_summary"},
                    },
                },
                "participant_action": {"kind": "speak", "response": {"mode": "text"}},
            },
        },
        "referee": {
            "enabled": True,
            "check_on": "after_scene",
            "rules": [
                {
                    "when": {"left": "SCENE.dynamic_schedule_summary", "op": "not_null", "right": True},
                    "result": {"end": "merged"},
                }
            ],
        },
    }, allow_unicode=True), encoding="utf-8")
    runtime = _runtime_for(str(script_path))
    runner = build_runner_for_session(runtime, dry_run=True)

    await runner.assign()
    await runner.start()
    await runtime.runtime_state.task

    assert runtime.session.metadata["interactive_session"]["result"] == "merged"


def test_runoff_tie_policy_marks_runoff_candidates():
    """tie_policy=runoff should not silently pick alphabetical winner."""
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["A", "B"]},
        "flow": {"type": "sequence", "scenes": ["vote"]},
        "scenes": {"vote": {"participants": {"static": []}}},
    })
    result = SceneExecutor()._selection(
        ctx,
        {"field": "vote", "tie_policy": "runoff"},
        [
            {"data": {"vote": "B"}},
            {"data": {"vote": "A"}},
        ],
        None,
    )

    assert result["winner"] is None
    assert result["needs_runoff"] is True
    assert result["runoff_candidates"] == ["A", "B"]


@pytest.mark.asyncio
async def test_human_controller_without_seat_uses_human_actor():
    """controller.type=human should default to the human seat when omitted."""
    human = _ScriptedActor("P1", [{"actor": "P1", "text": "human move", "data": None}])
    human.is_human = True
    agent = _ScriptedActor("P2", [{"actor": "P2", "text": "agent move", "data": None}])
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["P1", "P2"]},
        "flow": {"type": "sequence", "scenes": ["start"]},
        "scenes": {
            "start": {
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
            }
        },
    }, actors=[human, agent])

    response = await ControllerActionExecutor()._controller_response(
        ctx,
        ControllerActionSpec(enabled=True, controller={"type": "human"}, kind="free_text"),
        "cue",
    )

    assert response["actor"] == "P1"
    assert response["text"] == "human move"


@pytest.mark.asyncio
async def test_referee_inside_provider_can_call_async_agent():
    """referee evaluator llm/provider=inside should await a ccserver Agent-like client."""
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
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
            "evaluator": "llm",
            "provider": "inside",
            "result": {"end": "inside_done"},
        },
    }, actors=[_ScriptedActor("P1", [])])
    ctx.session_metadata["inside_agent"] = _AsyncInsideAgent('{"result": true}')

    result = await SceneExecutor().execute(ctx, ctx.script.scenes["start"])

    assert result == "inside_done"


@pytest.mark.asyncio
async def test_http_condition_ignores_runtime_only_extra(monkeypatch):
    """HTTP evaluator should not JSON-encode runtime-only Python handles."""
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["P1"]},
        "flow": {"type": "sequence", "scenes": ["start"]},
        "scenes": {"start": {"participants": {"static": []}}},
    }, actors=[_ScriptedActor("P1", [])])
    ctx.session_metadata["inside_agent"] = _AsyncInsideAgent('{"result": true}')

    def fail_urlopen(_request, timeout=0):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)

    passed = await ctx.condition_evaluator.evaluate_async(
        {"evaluator": "http", "url": "http://127.0.0.1/check", "fallback": True},
        ctx.state,
        actor=None,
        responses=[],
        extra=ctx.condition_extra(),
    )

    assert passed is True


@pytest.mark.asyncio
async def test_openchat_collects_one_speaker_per_turn(tmp_path):
    """openchat should be an open one-speaker-at-a-time conversation."""
    script_path = tmp_path / "openchat.yaml"
    script_path.write_text(yaml.safe_dump({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["A", "B"]},
        "flow": {"type": "sequence", "scenes": ["chat"]},
        "scenes": {
            "chat": {
                "participants": {"static": ["A", "B"]},
                "schedule": {"mode": "openchat", "max_turns": 3, "actor": "A"},
                "participant_action": {"kind": "speak", "response": {"mode": "text"}},
            }
        },
    }, allow_unicode=True), encoding="utf-8")
    runtime = _runtime_for(str(script_path))
    runner = build_runner_for_session(runtime, dry_run=True)

    await runner.assign()
    await runner.start()
    await runtime.runtime_state.task

    messages = [
        event for event in runtime.event_store.public
        if event.get("kind") == "interactive_message"
    ]
    assert len(messages) == 3
    assert [event["sender"] for event in messages] == ["A", "B", "A"]


@pytest.mark.asyncio
async def test_dynamic_child_openchat_uses_opening_and_first_speaker():
    """dynamic child openchat should honor AI-designed opening and first_speaker."""
    actors = [
        _ScriptedActor("A", [{"actor": "A", "text": "请 B 和 C 私聊", "data": None}]),
        _ScriptedActor("B", [{"actor": "B", "text": "B follows", "data": None}]),
        _ScriptedActor("C", [{"actor": "C", "text": "C opens", "data": None}]),
    ]
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["A", "B", "C"]},
        "flow": {"type": "sequence", "scenes": ["chat"]},
        "scenes": {
            "chat": {
                "participants": {"static": ["A", "B", "C"]},
                "scope": {"id": "public", "visibility": "public"},
                "schedule": {
                    "mode": "single",
                    "actor": "A",
                    "dynamic": {
                        "enabled": True,
                        "check_on": "after_message",
                        "detector": {
                            "patch": {
                                "type": "push_schedule",
                                "mode": "openchat",
                                "participants": ["B", "C"],
                                "max_turns": 2,
                                "first_speaker": "C",
                                "opening": "C 先开场，然后 B 回应。",
                            }
                        },
                    },
                },
                "participant_action": {"kind": "speak", "response": {"mode": "text"}},
            }
        },
    }, actors=actors)
    public_events = []
    ctx.emit_public = public_events.append

    await SceneExecutor().execute(ctx, ctx.script.scenes["chat"])

    messages = [event for event in public_events if event.get("kind") == "interactive_message"]
    assert [event["sender"] for event in messages] == ["A", "C", "B"]


@pytest.mark.asyncio
async def test_dynamic_openchat_partial_scope_defaults_public():
    """openchat schedule patches with partial scope should stay public by default."""
    actors = [
        _ScriptedActor("A", [{"actor": "A", "text": "start", "data": None}]),
        _ScriptedActor("B", [{"actor": "B", "text": "B opens", "data": None}]),
    ]
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["A", "B"]},
        "flow": {"type": "sequence", "scenes": ["chat"]},
        "scenes": {
            "chat": {
                "participants": {"static": ["A", "B"]},
                "schedule": {
                    "mode": "single",
                    "actor": "A",
                    "dynamic": {
                        "enabled": True,
                        "check_on": "after_message",
                        "detector": {
                            "patch": {
                                "type": "push_schedule",
                                "mode": "openchat",
                                "participants": ["B"],
                                "scope": {"id": "open_side_chat"},
                                "max_turns": 1,
                            }
                        },
                    },
                },
                "participant_action": {"kind": "speak", "response": {"mode": "text"}},
            }
        },
    }, actors=actors)
    public_events = []
    host_events = []
    ctx.emit_public = public_events.append
    ctx.emit_host = host_events.append

    await SceneExecutor().execute(ctx, ctx.script.scenes["chat"])

    public_messages = [
        event for event in public_events
        if event.get("kind") == "interactive_message" and event.get("sender") == "B"
    ]
    private_messages = [
        event for event in host_events
        if event.get("kind") == "interactive_message" and event.get("sender") == "B"
    ]
    assert public_messages and public_messages[0]["scope"] == "open_side_chat"
    assert private_messages == []


@pytest.mark.asyncio
async def test_dynamic_schedule_unknown_participants_do_not_pollute_journal():
    """Invalid runtime participants should reject schedule_patch before journal append."""
    actors = [
        _ScriptedActor("A", [{"actor": "A", "text": "ask unknown", "data": None}]),
    ]
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["A"]},
        "flow": {"type": "sequence", "scenes": ["chat"]},
        "scenes": {
            "chat": {
                "participants": {"static": ["A"]},
                "schedule": {
                    "mode": "single",
                    "actor": "A",
                    "dynamic": {
                        "enabled": True,
                        "check_on": "after_message",
                        "detector": {
                            "patch": {
                                "type": "push_schedule",
                                "mode": "openchat",
                                "participants": ["Z"],
                                "max_turns": 1,
                            }
                        },
                    },
                },
                "participant_action": {"kind": "speak", "response": {"mode": "text"}},
            }
        },
    }, actors=actors)
    host_events = []
    ctx.emit_host = host_events.append

    await SceneExecutor().execute(ctx, ctx.script.scenes["chat"])

    assert ctx.patch_journal.by_type("schedule_patch") == []
    assert any("参与者不在当前 actor 集合中" in event.get("message", "") for event in host_events)


@pytest.mark.asyncio
async def test_publication_supports_template_ref_and_private_players():
    """publication should render template/ref content and route player audiences."""
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["A"]},
        "flow": {"type": "sequence", "scenes": ["publish"]},
        "scenes": {
            "publish": {
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
                "publication": {
                    "messages": [
                        {
                            "audience": {"scope": "public"},
                            "content": {"template": "阶段 {GAME.phase}"},
                        }
                    ],
                    "disclosures": [
                        {
                            "audience": {"players": ["A"]},
                            "content": {"ref": "GAME.secret"},
                        }
                    ],
                },
            }
        },
    }, actors=[_ScriptedActor("A", [])])
    StateWriter(ctx.state).apply(SetAttr("GAME", "phase", "day"))
    StateWriter(ctx.state).apply(SetAttr("GAME", "secret", "seer-only"))
    public_events = []
    private_events = []
    ctx.emit_public = public_events.append
    ctx.emit_private = lambda seat_id, event: private_events.append({"seat_id": seat_id, **event})

    await SceneExecutor().execute(ctx, ctx.script.scenes["publish"])

    assert any(event.get("text") == "阶段 day" for event in public_events)
    assert private_events == [{
        "seat_id": "A",
        "kind": "interactive_disclosure",
        "runtime_type": "interactive_session",
        "scene": "publish",
        "audience": ["A"],
        "text": "seer-only",
    }]


@pytest.mark.asyncio
async def test_broadcast_effect_is_drained_in_interactive_session():
    """resolution broadcast should publish in interactive_session, not stay queued."""
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["A"]},
        "flow": {"type": "sequence", "scenes": ["broadcast"]},
        "scenes": {
            "broadcast": {
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
                "resolution": {
                    "effects": [
                        {
                            "type": "broadcast",
                            "scope": {"id": "public", "visibility": "public"},
                            "message": {"template": "选中 {GAME.choice}"},
                        }
                    ]
                },
            }
        },
    }, actors=[_ScriptedActor("A", [])])
    StateWriter(ctx.state).apply(SetAttr("GAME", "choice", "A"))
    public_events = []
    ctx.emit_public = public_events.append

    await SceneExecutor().execute(ctx, ctx.script.scenes["broadcast"])

    assert ctx.state.get_attr("GAME", "__pending_broadcasts") == []
    assert any(event.get("kind") == "interactive_broadcast" and event.get("text") == "选中 A" for event in public_events)


@pytest.mark.asyncio
async def test_runoff_policy_can_jump_to_configured_scene():
    """tie_policy=runoff should set RESOLUTION data and configured next target."""
    actors = [
        _ScriptedActor("A", [{"actor": "A", "text": "vote B", "data": {"vote": "B"}}]),
        _ScriptedActor("B", [{"actor": "B", "text": "vote A", "data": {"vote": "A"}}]),
    ]
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["A", "B"]},
        "flow": {"type": "sequence", "scenes": ["vote", "runoff_vote"]},
        "scenes": {
            "vote": {
                "participants": {"static": ["A", "B"]},
                "schedule": {"mode": "sequential"},
                "participant_action": {"kind": "vote", "response": {"mode": "structured", "schema": "vote"}},
                "resolution": {
                    "selection": {
                        "field": "vote",
                        "tie_policy": "runoff",
                        "runoff": {"to": "runoff_vote"},
                    }
                },
            },
            "runoff_vote": {
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
            },
        },
    }, actors=actors)

    await SceneExecutor().execute(ctx, ctx.script.scenes["vote"])

    assert ctx.state.get_attr("RESOLUTION", "needs_runoff") is True
    assert ctx.state.get_attr("RESOLUTION", "runoff_candidates") == ["A", "B"]
    assert ctx.session_metadata["interactive_next_target"] == "runoff_vote"


@pytest.mark.asyncio
async def test_inside_runtime_service_awaits_agent_run():
    """runtime service provider=inside should await a ccserver Agent-like run method."""
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["P1"]},
        "flow": {"type": "sequence", "scenes": ["start"]},
        "scenes": {"start": {"participants": {"static": []}}},
    })
    agent = _AsyncInsideAgent('{"text": "planned"}')
    ctx.session_metadata["inside_agent"] = agent

    result = await RuntimeServiceCaller().call_async(
        ctx,
        {"provider": "inside", "prompt": "plan"},
        "story_generator",
        ctx.full_context_payload(),
    )

    assert result["text"] == "planned"
    assert agent.prompts == ["plan"]


@pytest.mark.asyncio
async def test_schedule_timeout_skips_unfinished_simultaneous_actor():
    """schedule.timeout_ms should cancel only actors that exceed the limit."""

    class _SlowActor(_ScriptedActor):
        async def act(self, cue: str, collect=None) -> dict:
            await asyncio.sleep(0.05)
            return await super().act(cue, collect)

    actors = [
        _SlowActor("A", [{"actor": "A", "text": "late", "data": None}]),
        _ScriptedActor("B", [{"actor": "B", "text": "ready", "data": None}]),
    ]
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["A", "B"]},
        "flow": {"type": "sequence", "scenes": ["vote"]},
        "scenes": {"vote": {"participants": {"static": ["A", "B"]}}},
    }, actors=actors)
    host_events = []
    ctx.emit_host = host_events.append

    responses = await ParticipantActionExecutor().collect_many(
        ctx=ctx,
        actor_names=["A", "B"],
        action=ParticipantActionSpec(kind="speak", response={"mode": "text"}),
        scope=ScopeSpec(id="public", visibility="public"),
        participants=["A", "B"],
        mode="simultaneous",
        timeout_ms=10,
    )

    assert [response["actor"] for response in responses] == ["B"]
    assert any(event.get("kind") == "interactive_schedule_timeout" for event in host_events)


@pytest.mark.asyncio
async def test_dynamic_child_openchat_uses_planner_stop():
    """dynamic child openchat should ask its planner after each generated message."""
    actors = [
        _ScriptedActor("A", [{"actor": "A", "text": "请 B 和 C 开放聊", "data": None}]),
        _ScriptedActor("B", [{"actor": "B", "text": "planner ignored", "data": None}]),
        _ScriptedActor("C", [{"actor": "C", "text": "C opens", "data": None}]),
    ]
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["A", "B", "C"]},
        "flow": {"type": "sequence", "scenes": ["chat"]},
        "scenes": {
            "chat": {
                "participants": {"static": ["A", "B", "C"]},
                "scope": {"id": "public", "visibility": "public"},
                "schedule": {
                    "mode": "single",
                    "actor": "A",
                    "dynamic": {
                        "enabled": True,
                        "check_on": "after_message",
                        "detector": {
                            "patch": {
                                "type": "push_schedule",
                                "mode": "openchat",
                                "participants": ["B", "C"],
                                "max_turns": 3,
                                "first_speaker": "C",
                                "planner": {"provider": "plugin", "name": "stop_child_chat"},
                            }
                        },
                    },
                },
                "participant_action": {"kind": "speak", "response": {"mode": "text"}},
            }
        },
    }, actors=actors)
    ctx.plugin_registry.register_runtime_service("stop_child_chat", lambda _payload: {"stop": True})
    public_events = []
    ctx.emit_public = public_events.append

    await SceneExecutor().execute(ctx, ctx.script.scenes["chat"])

    messages = [event for event in public_events if event.get("kind") == "interactive_message"]
    assert [event["sender"] for event in messages] == ["A", "C"]


@pytest.mark.asyncio
async def test_runtime_service_input_include_flags_shape_payload():
    """runtime service input include flags should send a compact materialized payload."""
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["A"]},
        "flow": {"type": "sequence", "scenes": ["start"]},
        "scenes": {"start": {"participants": {"static": []}}},
    })
    StateWriter(ctx.state).apply(SetAttr("GAME", "secret", "open"))
    ctx.last_responses = [
        {"actor": "A", "text": "old", "data": None},
        {"actor": "A", "text": "new", "data": None},
    ]
    captured = []
    ctx.plugin_registry.register_runtime_service(
        "capture_input",
        lambda payload: captured.append(payload) or {"ok": True},
    )

    result = await RuntimeServiceCaller().call_async(
        ctx,
        {
            "provider": "plugin",
            "name": "capture_input",
            "input": {
                "include_state": True,
                "include_players": True,
                "include_recent_messages": True,
                "recent_limit": 1,
                "secret": {"ref": "GAME.secret"},
            },
        },
        "test_input",
        ctx.full_context_payload(),
    )

    assert result == {"ok": True}
    assert captured[0]["state"]["GAME"]["secret"] == "open"
    assert captured[0]["players"] == ["A"]
    assert captured[0]["recent_messages"] == [{"actor": "A", "text": "new", "data": None}]
    assert captured[0]["secret"] == "open"
    assert "last_responses" not in captured[0]


@pytest.mark.asyncio
async def test_state_machine_no_transition_match_stops_without_max_steps():
    """A state with unmatched transitions should stop, not loop until max_steps."""
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["A"]},
        "state": {"GAME": {"ready": False}},
        "flow": {
            "type": "state_machine",
            "initial": "start",
            "states": {
                "start": {
                    "scenes": ["noop"],
                    "transitions": [
                        {"to": "start", "when": {"left": "GAME.ready", "op": "equal", "right": True}}
                    ],
                }
            },
        },
        "scenes": {"noop": {"participants": {"static": []}, "schedule": {"mode": "none"}}},
    })
    host_events = []
    ctx.emit_host = host_events.append

    result = await FlowExecutor(max_steps=3).execute(ctx)

    assert result == "interactive_session_completed"
    assert any(event.get("kind") == "interactive_session_flow_stopped" for event in host_events)


@pytest.mark.asyncio
async def test_summarize_hook_writes_scene_summary():
    """hooks.on_exit type=summarize should write a deterministic text summary."""
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["A"]},
        "flow": {"type": "sequence", "scenes": ["talk"]},
        "scenes": {
            "talk": {
                "participants": {"static": ["A"]},
                "schedule": {"mode": "single", "actor": "A"},
                "participant_action": {"kind": "speak", "response": {"mode": "text"}},
                "hooks": {"on_exit": [{"type": "summarize", "to": "STORY.scene_summary"}]},
            }
        },
    }, actors=[_ScriptedActor("A", [{"actor": "A", "text": "hello", "data": None}])])

    await SceneExecutor().execute(ctx, ctx.script.scenes["talk"])

    assert ctx.state.get_attr("STORY", "scene_summary") == "A: hello"


@pytest.mark.asyncio
async def test_resolution_selection_source_controller_result():
    """resolution.selection.source should be able to read controller_result."""
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["A", "B"]},
        "flow": {"type": "sequence", "scenes": ["choice"]},
        "scenes": {
            "choice": {
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
                "controller_action": {
                    "enabled": True,
                    "controller": {"type": "plugin", "name": "pick_b"},
                    "kind": "choice",
                    "choices": [{"id": "A"}, {"id": "B"}],
                },
                "resolution": {
                    "selection": {
                        "source": "controller_result",
                        "field": "selected_choice",
                    }
                },
            }
        },
    })
    ctx.plugin_registry.register_runtime_service("pick_b", lambda _payload: {"data": {"choice": "B"}, "text": "B"})

    await SceneExecutor().execute(ctx, ctx.script.scenes["choice"])

    assert ctx.state.get_attr("RESOLUTION", "selected") == "B"


def test_add_transition_requires_existing_states():
    """add_transition patches should not create undeclared flow states."""
    ctx = _interactive_ctx({
        "runtime": {"type": "interactive_session"},
        "players": {"ids": ["A"]},
        "flow": {"type": "state_machine", "initial": "start", "states": {"start": {"scenes": ["noop"]}}},
        "scenes": {"noop": {"participants": {"static": []}, "schedule": {"mode": "none"}}},
    })

    with pytest.raises(ValueError, match="add_transition"):
        FreeInputExecutor()._validate_and_preview_flow_patch(
            ctx,
            {"type": "add_transition", "from": "start", "to": "missing"},
            "flow_patch",
        )

    assert ctx.patch_journal.by_type("flow_patch") == []
