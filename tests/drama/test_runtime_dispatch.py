"""Runtime dispatch tests."""

from types import SimpleNamespace

import pytest

from drama_engine.core.execution_models.fixed_flow import (
    BoardGameRunner,
    CardGameRunner,
    EconomyGameRunner,
    FixedFlowGameRunner,
    SocialDeductionGameRunner,
)
from drama_engine.core.ports.input import InputBridge
from drama_engine.core.ports.memory import (
    InMemoryRuntimeMemoryBackend,
    JsonlRuntimeMemoryBackend,
    RuntimeMemoryStore,
    configure_runtime_memory_backend,
)
from drama_engine.core.ports.views import BaseViewProjector
from drama_engine.core.runner.base import BasicGameRunner
from drama_engine.core.runner.config import RuntimeConfigParser
from drama_engine.core.runner.dispatch import build_runner_for_session, read_runtime_declaration
from drama_engine.core.session.lifecycle import RuntimeState
from drama_engine.core.session.ports import ServicePorts
from drama_engine.core.session.summary import SummaryProvider
from drama_engine.core.execution_models.dynamic_story import (
    LlmDmPolicy,
    NPCPolicy,
    StoryRuleChecker,
    StorySafetyBoundary,
    DynamicStoryPolicy,
    DynamicStoryRunner,
    DynamicStoryDomainRuntime,
    StoryLoop,
    WorldConsistencyChecker,
    WorldMemory,
    WorldStateWriter,
)
from drama_engine.core.execution_models.group_chat import (
    GroupChatPolicy,
    GroupChatLoop,
    GroupChatRunner,
    GroupChatDomainRuntime,
    TranscriptWriter,
)


def _write_script(tmp_path, runtime_yaml: str) -> str:
    """写入只含 runtime 的最小 YAML。"""
    path = tmp_path / "game.yaml"
    path.write_text(runtime_yaml, encoding="utf-8")
    return str(path)


def _runtime_for(script_path: str):
    """构造 build_runner_for_session 需要的最小 runtime 对象。"""
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
    """Minimal session object for runtime dispatch tests."""

    def __init__(self, script_path: str) -> None:
        self.script_path = script_path
        self.params = {}
        self.metadata = {}
        self.status = "lobby"
        self.seat_ids = ["Player_1", "Player_2"]

    def set_status(self, status: str) -> None:
        """Set session status like GameSessionState."""
        self.status = status


class _FakeEventStore:
    """Collect public and host events for runner tests."""

    def __init__(self) -> None:
        self.public = []
        self.host = []

    def append_public(self, event: dict) -> None:
        """Append a public event."""
        self.public.append(event)

    def append_host(self, event: dict) -> None:
        """Append a host event."""
        self.host.append(event)

    def append_private(self, seat_id: str, event: dict) -> None:
        """Append a private event."""
        _ = seat_id
        self.host.append(event)


class _FakeActionService:
    """Minimal action service for actor runtime tests."""

    session_id = "fake-session"


def test_read_runtime_declaration_defaults_to_game_session(tmp_path):
    """未声明 runtime 时默认 game_session。"""
    script_path = _write_script(tmp_path, "meta: {title: 测试}\n")

    declaration = read_runtime_declaration(script_path)

    assert declaration.type == "game_session"
    assert declaration.config == {}


def test_build_runner_uses_game_session_runner(tmp_path):
    """game_session runtime 分派到 SocialDeductionGameRunner。"""
    script_path = _write_script(tmp_path, "runtime: {type: game_session}\n")
    runtime = _runtime_for(script_path)

    runner = build_runner_for_session(runtime=runtime, dry_run=True)

    assert isinstance(runner, SocialDeductionGameRunner)
    assert isinstance(runner, FixedFlowGameRunner)
    assert isinstance(runner, BasicGameRunner)
    assert runtime.session.metadata["runtime_type"] == "game_session"


def test_jsonl_runtime_memory_backend_persists_namespace_events(tmp_path):
    """JSONL long-term memory backend should persist and filter by namespace."""
    path = tmp_path / "runtime-memory.jsonl"
    backend = JsonlRuntimeMemoryBackend(path)

    backend.append("group_chat:topic-a", {"text": "first"})
    backend.append("group_chat:topic-b", {"text": "other"})
    backend.append("group_chat:topic-a", {"text": "second"})

    restored = JsonlRuntimeMemoryBackend(path)
    assert restored.query("group_chat:topic-a") == [{"text": "first"}, {"text": "second"}]
    assert restored.query("group_chat:topic-a", limit=1) == [{"text": "second"}]
    assert restored.query("group_chat:topic-a", limit=0) == []


def test_configure_runtime_memory_backend_binds_jsonl_backend(tmp_path):
    """Runtime config should bind a JSONL memory backend through the shared port."""
    store = RuntimeMemoryStore()
    path = tmp_path / "memory.jsonl"

    backend = configure_runtime_memory_backend(
        store,
        {"memory_backend": {"type": "jsonl", "path": str(path)}},
    )

    assert isinstance(backend, JsonlRuntimeMemoryBackend)
    store.remember_long_term("dynamic_story:world", {"text": "persisted"})
    assert JsonlRuntimeMemoryBackend(path).query("dynamic_story:world") == [{"text": "persisted"}]


def test_build_runner_specializes_fixed_flow_board_scripts(tmp_path):
    """带 board scene 的 game_session 应分派到 BoardGameRunner。"""
    script_path = _write_script(
        tmp_path,
        "runtime: {type: game_session}\nflow: {scenes: [{name: move, scene_type: board}]}\n",
    )
    runtime = _runtime_for(script_path)

    runner = build_runner_for_session(runtime=runtime, dry_run=True)

    assert isinstance(runner, BoardGameRunner)
    assert isinstance(runner, FixedFlowGameRunner)


def test_build_runner_specializes_fixed_flow_card_scripts(tmp_path):
    """带 cards 扩展的 game_session 应分派到 CardGameRunner。"""
    script_path = _write_script(
        tmp_path,
        "runtime: {type: game_session}\nextensions: {cards: {enabled: true}}\n",
    )
    runtime = _runtime_for(script_path)

    runner = build_runner_for_session(runtime=runtime, dry_run=True)

    assert isinstance(runner, CardGameRunner)
    assert isinstance(runner, FixedFlowGameRunner)


def test_build_runner_specializes_fixed_flow_economy_scripts(tmp_path):
    """带 economy 扩展的 game_session 应分派到 EconomyGameRunner。"""
    script_path = _write_script(
        tmp_path,
        "runtime: {type: game_session}\nextensions: {economy: {enabled: true}}\n",
    )
    runtime = _runtime_for(script_path)

    runner = build_runner_for_session(runtime=runtime, dry_run=True)

    assert isinstance(runner, EconomyGameRunner)
    assert isinstance(runner, FixedFlowGameRunner)


def test_group_chat_runtime_dispatches_to_group_chat_runner(tmp_path):
    """group_chat runtime 应分派到 GroupChatRunner。"""
    script_path = _write_script(tmp_path, "runtime: {type: group_chat, config: {topic: 测试话题}}\n")
    runtime = _runtime_for(script_path)

    runner = build_runner_for_session(runtime=runtime, dry_run=True)

    assert isinstance(runner, GroupChatRunner)
    assert isinstance(runner, BasicGameRunner)
    assert runner.session_state is runtime.service.session_state
    assert runner.context.input_bridge is runtime.input_bridge
    assert runner.context.config_parser is runtime.runtime_config_parser
    assert runtime.session.metadata["runtime_type"] == "group_chat"


@pytest.mark.asyncio
async def test_group_chat_runner_can_assign_start_and_end(tmp_path):
    """GroupChatRunner 应能完成基础 room event loop。"""
    script_path = _write_script(tmp_path, "runtime: {type: group_chat, config: {topic: 测试话题, max_rounds: 1}}\n")
    runtime = _runtime_for(script_path)
    runtime.memory_store.bind_backend(InMemoryRuntimeMemoryBackend())
    runner = build_runner_for_session(runtime=runtime, dry_run=True)

    await runner.assign()
    assert isinstance(runner._domain_runtime, GroupChatDomainRuntime)
    assert isinstance(runner._domain_runtime.projector, BaseViewProjector)
    assert isinstance(runner._domain_runtime.transcript_writer, TranscriptWriter)
    assert runner.context.actor_runtime.cast is not None
    assert set(runner.context.actor_runtime.cast.all_names()) == {"Player_1", "Player_2"}
    await runner.start()
    await runtime.runtime_state.task

    assert isinstance(runner._loop, GroupChatLoop)
    assert runtime.session.status == "ended"
    assert any(event["kind"] == "group_chat_message" for event in runtime.event_store.public)
    assert runtime.session.metadata["group_chat"]["transcript_size"] == 2
    assert runtime.session.metadata["group_chat"]["transcript"]
    assert runtime.session.metadata["group_chat"]["transcript"][0]["source"] == "actor"
    assert runtime.session.metadata["group_chat"]["transcript"][0]["text"].startswith("(dry-run Player_")
    assert runtime.session.metadata["group_chat"]["transcript_summary"]
    assert len(runner.context.memory_store.list("group_chat.transcript")) == 2
    assert runner.context.memory_store.latest("group_chat.summary")["summary"]
    assert len(runner.context.memory_store.recall_long_term("group_chat:测试话题")) == 2
    assert any(event.get("view_id") == "group-chat-transcript" for event in runtime.event_store.public)


@pytest.mark.asyncio
async def test_group_chat_runner_uses_policy_component(tmp_path):
    """GroupChatRunner 应通过 policy 构造 actor 上下文和 cue。"""

    class RecordingGroupChatPolicy(GroupChatPolicy):
        """Record policy method calls for the runner boundary test."""

        def __init__(self) -> None:
            super().__init__(topic="测试话题", role_prompts={})
            self.perception_calls = 0
            self.cue_calls = 0

        def perception_for(self, state):
            """Return a deterministic test perception."""
            self.perception_calls += 1
            return {"scope": "group_chat", "sender": "test-policy", "text": state.topic}

        def cue_for(self, speaker, round_index, transcript):
            """Return a deterministic test cue."""
            self.cue_calls += 1
            return f"policy cue {speaker} {round_index} {len(transcript)}"

    script_path = _write_script(tmp_path, "runtime: {type: group_chat, config: {topic: 测试话题, max_rounds: 1}}\n")
    runtime = _runtime_for(script_path)
    runner = build_runner_for_session(runtime=runtime, dry_run=True)
    policy = RecordingGroupChatPolicy()

    await runner.assign()
    runner._domain_runtime.policy = policy
    await runner.start()
    await runtime.runtime_state.task

    assert policy.perception_calls == 2
    assert policy.cue_calls == 2


@pytest.mark.asyncio
async def test_group_chat_runner_reads_and_writes_long_term_memory(tmp_path):
    """GroupChatRunner 应通过 RuntimeMemoryStore 读写跨局长期记忆。"""
    script_path = _write_script(
        tmp_path,
        "runtime: {type: group_chat, config: {topic: 测试话题, max_rounds: 1}}\n",
    )
    runtime = _runtime_for(script_path)
    backend = InMemoryRuntimeMemoryBackend()
    runtime.memory_store.bind_backend(backend)
    backend.append("group_chat:测试话题", {"text": "历史讨论：先确认目标"})
    runner = build_runner_for_session(runtime=runtime, dry_run=True)

    await runner.assign()
    perception = runner._domain_runtime.policy.perception_for(runner._state)
    await runner.start()
    await runtime.runtime_state.task

    memories = backend.query("group_chat:测试话题")
    assert "历史讨论：先确认目标" in perception["text"]
    assert any(item.get("speaker") == "Player_1" for item in memories)


@pytest.mark.asyncio
async def test_dynamic_story_runner_can_assign_start_and_end(tmp_path):
    """DynamicStoryRunner 应能完成基础 story beat loop。"""
    script_path = _write_script(
        tmp_path,
        "runtime: {type: dynamic_story, config: {premise: 测试剧情, beats: [开场, 转折], free_actions: [调查钟楼]}}\n",
    )
    runtime = _runtime_for(script_path)
    runner = build_runner_for_session(runtime=runtime, dry_run=True)

    assert isinstance(runner, DynamicStoryRunner)
    await runner.assign()
    assert isinstance(runner._domain_runtime, DynamicStoryDomainRuntime)
    assert isinstance(runner._domain_runtime.projector, BaseViewProjector)
    assert isinstance(runner._domain_runtime.world_writer, WorldStateWriter)
    assert runner.context.actor_runtime.cast is not None
    assert set(runner.context.actor_runtime.cast.all_names()) == {"Player_1", "Player_2"}
    await runner.start()
    await runtime.runtime_state.task

    assert isinstance(runner._loop, StoryLoop)
    assert runtime.session.status == "ended"
    assert any(event["kind"] == "dynamic_story_beat" for event in runtime.event_store.public)
    assert any(event["kind"] == "dynamic_story_action" for event in runtime.event_store.public)
    assert any(event["kind"] == "dynamic_story_ruling" for event in runtime.event_store.public)
    action_events = [event for event in runtime.event_store.public if event["kind"] == "dynamic_story_action"]
    assert action_events[0]["text"].startswith("(dry-run Player_")
    assert runtime.session.metadata["dynamic_story"]["world_memory"]["events"]
    assert runner.context.memory_store.list("dynamic_story.memory")
    assert runner.context.memory_store.latest("dynamic_story.world")["events"]
    assert any(event.get("view_id") == "dynamic-story-memory" for event in runtime.event_store.public)


@pytest.mark.asyncio
async def test_dynamic_story_runner_uses_policy_component(tmp_path):
    """DynamicStoryRunner 应通过 policy 选择 actor、解释 action 并执行 DM 裁定。"""

    class RecordingDynamicStoryPolicy(DynamicStoryPolicy):
        """Record policy method calls for the runner boundary test."""

        def __init__(self) -> None:
            super().__init__()
            self.select_calls = 0
            self.perception_calls = 0
            self.cue_calls = 0
            self.interpret_calls = 0
            self.adjudicate_calls = 0

        def select_actor(self, state, beat_index):
            """Always select the second test player."""
            self.select_calls += 1
            _ = beat_index
            return state.players[1]

        def perception_for(self, state, world):
            """Return a deterministic test perception."""
            self.perception_calls += 1
            return {"scope": "dynamic_story", "sender": "test-policy", "text": state.world_name}

        def cue_for(self, actor_name, beat_index, action_hint):
            """Return a deterministic test cue."""
            self.cue_calls += 1
            return f"policy cue {actor_name} {beat_index} {action_hint}"

        def interpret_action(self, actor_name, text, index):
            """Return a deterministic action event."""
            self.interpret_calls += 1
            return {
                "kind": "dynamic_story_action",
                "index": index,
                "actor": actor_name,
                "text": f"policy interpreted {text}",
                "intent": "test",
            }

        def adjudicate(self, action, world):
            """Return a deterministic ruling event and update world memory."""
            self.adjudicate_calls += 1
            event = {
                "kind": "dynamic_story_ruling",
                "index": action["index"],
                "actor": action["actor"],
                "intent": action["intent"],
                "consequence": "policy ruling",
                "location": "test",
            }
            world.remember(event)
            return event

    script_path = _write_script(
        tmp_path,
        "runtime: {type: dynamic_story, config: {premise: 测试剧情, beats: [开场], free_actions: [调查钟楼]}}\n",
    )
    runtime = _runtime_for(script_path)
    runner = build_runner_for_session(runtime=runtime, dry_run=True)
    policy = RecordingDynamicStoryPolicy()

    await runner.assign()
    runner._domain_runtime.policy = policy
    await runner.start()
    await runtime.runtime_state.task

    assert policy.select_calls == 1
    assert policy.perception_calls == 1
    assert policy.cue_calls == 1
    assert policy.interpret_calls == 1
    assert policy.adjudicate_calls == 1
    action_events = [event for event in runtime.event_store.public if event["kind"] == "dynamic_story_action"]
    ruling_events = [event for event in runtime.event_store.public if event["kind"] == "dynamic_story_ruling"]
    assert action_events[0]["actor"] == "Player_2"
    assert action_events[0]["text"].startswith("policy interpreted")
    assert ruling_events[0]["consequence"] == "policy ruling"


@pytest.mark.asyncio
async def test_dynamic_story_runner_reads_and_writes_long_term_memory(tmp_path):
    """DynamicStoryRunner 应通过 RuntimeMemoryStore 读写跨局长期世界记忆。"""
    script_path = _write_script(
        tmp_path,
        "runtime: {type: dynamic_story, config: {world_name: 测试世界, premise: 测试剧情, beats: [开场], free_actions: [调查钟楼]}}\n",
    )
    runtime = _runtime_for(script_path)
    backend = InMemoryRuntimeMemoryBackend()
    runtime.memory_store.bind_backend(backend)
    backend.append("dynamic_story:测试世界", {"text": "上一局钟楼已经坍塌"})
    runner = build_runner_for_session(runtime=runtime, dry_run=True)

    await runner.assign()
    perception = runner._domain_runtime.policy.perception_for(runner._state, runner._world)
    await runner.start()
    await runtime.runtime_state.task

    memories = backend.query("dynamic_story:测试世界")
    assert "上一局钟楼已经坍塌" in perception["text"]
    assert any(item.get("kind") == "dynamic_story_beat" for item in memories)


@pytest.mark.asyncio
async def test_group_chat_runner_binds_jsonl_memory_and_uses_phase_config(tmp_path):
    """GroupChatRunner should bind configured memory backend and use policy phases."""
    memory_path = tmp_path / "group-memory.jsonl"
    script_path = _write_script(
        tmp_path,
        (
            "runtime:\n"
            "  type: group_chat\n"
            "  config:\n"
            "    topic: 测试话题\n"
            "    max_rounds: 1\n"
            f"    memory_backend: {{type: jsonl, path: '{memory_path}'}}\n"
            "    policy:\n"
            "      discussion_phases: [提出方案]\n"
            "      room_rules: [必须回应主题]\n"
            "      max_context_items: 1\n"
        ),
    )
    runtime = _runtime_for(script_path)
    runner = build_runner_for_session(runtime=runtime, dry_run=True)

    await runner.assign()
    cue = runner._domain_runtime.policy.cue_for("Player_1", 1, [])
    await runner.start()
    await runtime.runtime_state.task

    assert "提出方案" in cue
    assert "必须回应主题" in cue
    assert JsonlRuntimeMemoryBackend(memory_path).query("group_chat:测试话题")


@pytest.mark.asyncio
async def test_dynamic_story_runner_supports_all_players_policy_mode(tmp_path):
    """DynamicStoryRunner policy config should allow every player to act per beat."""
    script_path = _write_script(
        tmp_path,
        (
            "runtime:\n"
            "  type: dynamic_story\n"
            "  config:\n"
            "    world_name: 测试世界\n"
            "    premise: 测试剧情\n"
            "    beats: [开场]\n"
            "    action_mode: all_players\n"
            "    default_action_hint: 每名玩家描述一个行动\n"
            "    policy:\n"
            "      dm:\n"
            "        tone: tense\n"
        ),
    )
    runtime = _runtime_for(script_path)
    runner = build_runner_for_session(runtime=runtime, dry_run=True)

    await runner.assign()
    await runner.start()
    await runtime.runtime_state.task

    action_events = [event for event in runtime.event_store.public if event["kind"] == "dynamic_story_action"]
    ruling_events = [event for event in runtime.event_store.public if event["kind"] == "dynamic_story_ruling"]
    assert {event["actor"] for event in action_events} == {"Player_1", "Player_2"}
    assert len(ruling_events) == 2
    assert all("tense" in event["consequence"] for event in ruling_events)


def test_llm_dm_policy_uses_injected_client():
    """LlmDmPolicy should adapt a real injected LLM client behind DMPolicy."""

    class FakeLlmClient:
        """Fake sync LLM client for the DM adapter contract."""

        def __init__(self) -> None:
            self.prompt = ""

        def generate_ruling(self, prompt, action, world):
            """Return a deterministic ruling text."""
            self.prompt = prompt
            return f"LLM 裁定：{action['actor']} 在 {world.state.get('last_location')} 推进剧情"

    world = WorldMemory({"last_location": "钟楼"})
    policy = LlmDmPolicy(llm_client=FakeLlmClient(), tone="tense")
    action = {
        "index": 1,
        "actor": "Player_1",
        "intent": "investigate",
        "text": "调查钟楼",
    }

    ruling = policy.adjudicate(action, world)

    assert ruling["source"] == "llm_dm"
    assert "LLM 裁定" in ruling["consequence"]
    assert world.events[-1]["source"] == "llm_dm"


@pytest.mark.asyncio
async def test_dynamic_story_policy_safety_rule_and_npc_pipeline(tmp_path):
    """DynamicStory should run safety, rule checker, and NPC policy in order."""
    script_path = _write_script(
        tmp_path,
        (
            "runtime:\n"
            "  type: dynamic_story\n"
            "  config:\n"
            "    world_name: 测试世界\n"
            "    premise: 测试剧情\n"
            "    beats: [开场]\n"
            "    free_actions: [禁术调查钟楼]\n"
            "    policy:\n"
            "      interpreter:\n"
            "        intent_keywords:\n"
            "          investigate: [调查]\n"
            "      safety:\n"
            "        forbidden_keywords: ['(dry-run']\n"
            "        replacement_text: 调查钟楼\n"
            "      rules:\n"
            "        allowed_intents: [investigate]\n"
            "      npc:\n"
            "        npcs:\n"
            "          - name: 守门人\n"
            "            trigger_keywords: [调查]\n"
            "            response: 守门人提醒你注意钟声。\n"
        ),
    )
    runtime = _runtime_for(script_path)
    runner = build_runner_for_session(runtime=runtime, dry_run=True)

    await runner.assign()
    await runner.start()
    await runtime.runtime_state.task

    public_events = runtime.event_store.public
    safety_events = [event for event in public_events if event["kind"] == "dynamic_story_safety_boundary"]
    action_events = [event for event in public_events if event["kind"] == "dynamic_story_action"]
    npc_events = [event for event in public_events if event["kind"] == "dynamic_story_npc_reaction"]
    rule_blocks = [
        event
        for event in public_events
        if event["kind"] == "dynamic_story_rule_check" and event["allowed"] is False
    ]
    assert safety_events and safety_events[0]["replacement_text"] == "调查钟楼"
    assert action_events and action_events[0]["intent"] == "investigate"
    assert npc_events and npc_events[0]["npc"] == "守门人"
    assert rule_blocks == []


@pytest.mark.asyncio
async def test_dynamic_story_world_consistency_blocks_bad_ruling(tmp_path):
    """World consistency checker should block impossible rulings and restore world memory."""
    script_path = _write_script(
        tmp_path,
        (
            "runtime:\n"
            "  type: dynamic_story\n"
            "  config:\n"
            "    world_name: 测试世界\n"
            "    premise: 测试剧情\n"
            "    world_state: {last_location: 禁地}\n"
            "    beats: [开场]\n"
            "    free_actions: [观察]\n"
            "    policy:\n"
            "      world_consistency:\n"
            "        known_locations: [大厅]\n"
            "        allow_unknown_location: false\n"
        ),
    )
    runtime = _runtime_for(script_path)
    runner = build_runner_for_session(runtime=runtime, dry_run=True)

    await runner.assign()
    await runner.start()
    await runtime.runtime_state.task

    public_events = runtime.event_store.public
    consistency_events = [event for event in public_events if event["kind"] == "dynamic_story_consistency_check"]
    ruling_events = [event for event in public_events if event["kind"] == "dynamic_story_ruling"]
    assert consistency_events and consistency_events[0]["allowed"] is False
    assert ruling_events == []
    assert all(event.get("kind") != "dynamic_story_ruling" for event in runner._world.events)
