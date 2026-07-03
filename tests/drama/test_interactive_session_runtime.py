"""interactive_session runtime tests."""

from types import SimpleNamespace

import pytest

from drama_engine.core.dsl.components.conditions import ConditionEvaluator
from drama_engine.core.engine import SetAttr, State, StateWriter, Vocabulary
from drama_engine.core.ports.input import InputBridge
from drama_engine.core.ports.memory import RuntimeMemoryStore
from drama_engine.core.runner.config import RuntimeConfigParser
from drama_engine.core.runner.dispatch import build_runner_for_session, read_runtime_declaration
from drama_engine.core.runtime.interactive_session import InteractiveSessionRunner
from drama_engine.core.runtime.interactive_session.compiler import InteractiveSessionCompiler
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
