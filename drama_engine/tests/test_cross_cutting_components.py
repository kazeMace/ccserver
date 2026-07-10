"""跨模式组件 + NarrativeJournal 集成测试。"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting import (
    InputGuard,
    OutputGuard,
    Planner,
    ChoiceDesigner,
    AssetResolver,
    GuardResult,
    PlanResult,
    AssetMatch,
    GenerationInput,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.registry import (
    FreeInputComponentRegistry,
    build_default_free_input_component_registry,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.guards import (
    CharacterExistenceInputGuard,
    ContentSafetyInputGuard,
    OutputCharacterExistenceGuard,
    SchemaConformanceGuard,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.planners import (
    NullPlanner,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.choice_designers import (
    PassthroughChoiceDesigner,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.asset_resolvers import (
    TagMatcherAssetResolver,
)
from drama_engine.core.runtime.interactive_session.narrative_journal import (
    NarrativeJournal,
    NarrativeNode,
    PlayerAction,
    ContentBlock,
    JsonFileNarrativeStore,
)


# ════════════════════════════════════════
# 测试 InputGuard
# ════════════════════════════════════════


class TestCharacterExistenceInputGuard:
    """角色存在性输入守卫测试。"""

    @pytest.fixture
    def guard(self):
        return CharacterExistenceInputGuard({})

    @pytest.fixture
    def characters(self):
        return [
            {"name": "Nora", "names": "Nora"},
            {"name": "Marco", "names": "Marco,Marco Diaz"},
            {"name": "Leila", "names": "Leila"},
        ]

    @pytest.mark.asyncio
    async def test_valid_input_passes(self, guard, characters):
        """提及存在的角色应通过。"""
        payload = {"text": "I want to talk to Marco", "characters": characters}
        result = await guard.check(payload, None)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_unknown_character_rejected(self, guard, characters):
        """提及不存在的角色应被拒绝。"""
        payload = {"text": "I want to talk to Sebastian", "characters": characters}
        result = await guard.check(payload, None)
        assert result.passed is False
        assert "Sebastian" in result.reason

    @pytest.mark.asyncio
    async def test_empty_input_passes(self, guard, characters):
        """空输入应通过。"""
        payload = {"text": "", "characters": characters}
        result = await guard.check(payload, None)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_no_characters_passes(self, guard):
        """无角色列表时应通过。"""
        payload = {"text": "Hello world", "characters": []}
        result = await guard.check(payload, None)
        assert result.passed is True


class TestContentSafetyInputGuard:
    """内容安全输入守卫测试。"""

    @pytest.mark.asyncio
    async def test_clean_input_passes(self):
        guard = ContentSafetyInputGuard({"blocked_patterns": ["hack", "exploit"]})
        result = await guard.check({"text": "I want to open the door"}, None)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_blocked_pattern_rejected(self):
        guard = ContentSafetyInputGuard({"blocked_patterns": ["hack", "exploit"]})
        result = await guard.check({"text": "Let me hack into the system"}, None)
        assert result.passed is False
        assert "不适当" in result.reason


# ════════════════════════════════════════
# 测试 OutputGuard
# ════════════════════════════════════════


class TestOutputCharacterExistenceGuard:
    """输出角色存在性守卫测试。"""

    @pytest.fixture
    def guard(self):
        return OutputCharacterExistenceGuard({})

    @pytest.fixture
    def characters(self):
        return [{"name": "Nora"}, {"name": "Marco"}]

    @pytest.mark.asyncio
    async def test_valid_dialogue_passes(self, guard, characters):
        payload = {
            "dialogue_history": [
                {"speaker": "Nora", "text": "Hello"},
                {"speaker": "Marco", "text": "Hi"},
                {"speaker": "narrator", "text": "They looked at each other"},
            ],
            "_characters": characters,
        }
        result = await guard.check(payload, None)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_invalid_speaker_rejected(self, guard, characters):
        payload = {
            "dialogue_history": [
                {"speaker": "Nora", "text": "Hello"},
                {"speaker": "Ghost", "text": "Boo"},
            ],
            "_characters": characters,
        }
        result = await guard.check(payload, None)
        assert result.passed is False
        assert "Ghost" in result.reason


class TestSchemaConformanceGuard:
    """输出结构守卫测试。"""

    @pytest.fixture
    def guard(self):
        return SchemaConformanceGuard({})

    @pytest.mark.asyncio
    async def test_valid_narration_passes(self, guard):
        result = await guard.check({"narration": "Something happened."}, None)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_valid_dialogue_passes(self, guard):
        result = await guard.check({
            "dialogue_history": [{"speaker": "A", "text": "hi"}]
        }, None)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_empty_content_rejected(self, guard):
        result = await guard.check({"narration": "", "dialogue_history": []}, None)
        assert result.passed is False
        assert "为空" in result.reason

    @pytest.mark.asyncio
    async def test_invalid_choice_rejected(self, guard):
        result = await guard.check({
            "narration": "text",
            "choices": [{"id": "a"}],  # 缺少 text
        }, None)
        assert result.passed is False
        assert "text" in result.reason


# ════════════════════════════════════════
# 测试 Planner
# ════════════════════════════════════════


class TestNullPlanner:
    """空规划器测试。"""

    @pytest.mark.asyncio
    async def test_returns_empty_plan(self):
        planner = NullPlanner({})
        result = await planner.plan("test action", {}, None)
        assert result.title == ""
        assert result.synopsis == ""
        assert result.characters_involved == []


# ════════════════════════════════════════
# 测试 ChoiceDesigner
# ════════════════════════════════════════


class TestPassthroughChoiceDesigner:
    """透传选项设计器测试。"""

    @pytest.mark.asyncio
    async def test_passes_through_choices(self):
        designer = PassthroughChoiceDesigner({})
        narration = {"choices": [{"id": "a", "text": "Option A"}, {"id": "b", "text": "Option B"}]}
        result = await designer.design_choices(narration, {}, None)
        assert len(result) == 2
        assert result[0]["id"] == "a"

    @pytest.mark.asyncio
    async def test_no_choices_returns_empty(self):
        designer = PassthroughChoiceDesigner({})
        result = await designer.design_choices({"narration": "text"}, {}, None)
        assert result == []


# ════════════════════════════════════════
# 测试 AssetResolver
# ════════════════════════════════════════


class TestTagMatcherAssetResolver:
    """标签匹配资产解析器测试。"""

    @pytest.fixture
    def resolver(self):
        return TagMatcherAssetResolver({})

    @pytest.fixture
    def asset_pool(self):
        return [
            {"id": "bg_garden", "tags": ["garden", "night", "romantic"], "path": "/bg/garden.png", "role": "background"},
            {"id": "bg_office", "tags": ["office", "day", "formal"], "path": "/bg/office.png", "role": "background"},
            {"id": "bg_street", "tags": ["street", "night", "urban"], "path": "/bg/street.png", "role": "background"},
        ]

    @pytest.mark.asyncio
    async def test_matches_by_tags(self, resolver, asset_pool):
        content = {"characters_involved": [], "title": "night garden", "asset_hints": {}}
        results = await resolver.resolve(content, asset_pool, None)
        assert len(results) > 0
        # garden + night 应该排第一
        assert results[0].asset_id == "bg_garden"

    @pytest.mark.asyncio
    async def test_empty_pool_returns_empty(self, resolver):
        results = await resolver.resolve({"title": "test"}, [], None)
        assert results == []


# ════════════════════════════════════════
# 测试 Registry
# ════════════════════════════════════════


class TestFreeInputComponentRegistry:
    """组件注册表测试。"""

    def test_build_default_registry(self):
        registry = build_default_free_input_component_registry()
        registered = registry.list_registered()
        assert "character_existence" in registered["input_guards"]
        assert "content_safety" in registered["input_guards"]
        assert "output_character_existence" in registered["output_guards"]
        assert "schema_conformance" in registered["output_guards"]
        assert "null_planner" in registered["planners"]
        assert "passthrough" in registered["choice_designers"]
        assert "tag_matcher" in registered["asset_resolvers"]

    def test_resolve_unknown_returns_empty(self):
        registry = build_default_free_input_component_registry()
        guards = registry.resolve_input_guards([{"name": "nonexistent"}])
        assert guards == []

    def test_resolve_none_planner(self):
        registry = build_default_free_input_component_registry()
        assert registry.resolve_planner(None) is None
        assert registry.resolve_choice_designer(None) is None
        assert registry.resolve_asset_resolver(None) is None

    def test_custom_registration(self):
        registry = FreeInputComponentRegistry()

        class MyGuard(InputGuard):
            async def check(self, payload, ctx):
                return GuardResult(passed=True)

        registry.register_input_guard("my_guard", MyGuard)
        assert registry.has_input_guard("my_guard")
        guards = registry.resolve_input_guards([{"name": "my_guard"}])
        assert len(guards) == 1
        assert isinstance(guards[0], MyGuard)


# ════════════════════════════════════════
# 测试 NarrativeJournal
# ════════════════════════════════════════


class TestNarrativeJournal:
    """叙事日志测试。"""

    @pytest.fixture
    def journal(self, tmp_path):
        store = JsonFileNarrativeStore(str(tmp_path / "journal"))
        return NarrativeJournal(store=store)

    @pytest.mark.asyncio
    async def test_record_transition(self, journal):
        node = await journal.record_transition(
            session_id="s1", user_id="u1",
            from_scene_id="scene_a", to_scene_id="scene_b",
            player_action=PlayerAction(type="choice", value="go", choice_id="go"),
            title="Go to B",
        )
        assert node.source == "preset"
        assert node.preset_scene_id == "scene_b"
        assert node.depth == 0

    @pytest.mark.asyncio
    async def test_record_generated(self, journal):
        parent = await journal.record_transition(
            session_id="s1", user_id="u1",
            from_scene_id="a", to_scene_id="b",
            player_action=PlayerAction(type="choice", value="x"),
        )
        child = await journal.record_generated(
            session_id="s1", user_id="u1",
            parent_node_id=parent.node_id,
            player_action=PlayerAction(type="free_input", value="open door"),
            title="The Door",
            synopsis="You opened the door.",
            content=[ContentBlock(type="narration", text="The door creaks open.")],
        )
        assert child.depth == 1
        assert child.parent_id == parent.node_id
        assert child.source == "generated"

    @pytest.mark.asyncio
    async def test_get_tree(self, journal):
        n1 = await journal.record_transition(
            session_id="s1", user_id="u1",
            from_scene_id="a", to_scene_id="b",
            player_action=PlayerAction(type="continue"),
        )
        n2 = await journal.record_generated(
            session_id="s1", user_id="u1",
            parent_node_id=n1.node_id,
            player_action=PlayerAction(type="free_input", value="look"),
            title="Look around",
        )
        tree = await journal.get_tree("s1", "u1")
        assert len(tree) == 2

    @pytest.mark.asyncio
    async def test_get_path(self, journal):
        n1 = await journal.record_transition(
            session_id="s1", user_id="u1",
            from_scene_id="a", to_scene_id="b",
            player_action=PlayerAction(type="continue"),
        )
        n2 = await journal.record_generated(
            session_id="s1", user_id="u1",
            parent_node_id=n1.node_id,
            player_action=PlayerAction(type="free_input", value="x"),
            title="Step 2",
        )
        n3 = await journal.record_generated(
            session_id="s1", user_id="u1",
            parent_node_id=n2.node_id,
            player_action=PlayerAction(type="free_input", value="y"),
            title="Step 3",
        )
        path = await journal.get_path("s1", "u1")
        assert len(path) == 3
        assert path[0].node_id == n1.node_id
        assert path[2].node_id == n3.node_id

    @pytest.mark.asyncio
    async def test_parent_children_relation(self, journal):
        parent = await journal.record_transition(
            session_id="s1", user_id="u1",
            from_scene_id="a", to_scene_id="b",
            player_action=PlayerAction(type="continue"),
        )
        child = await journal.record_generated(
            session_id="s1", user_id="u1",
            parent_node_id=parent.node_id,
            player_action=PlayerAction(type="free_input", value="x"),
            title="Child",
        )
        updated_parent = await journal.get_node(parent.node_id)
        assert child.node_id in updated_parent.children_ids


# ════════════════════════════════════════
# 测试 FreeInputExecutor 跨模式组件集成
# ════════════════════════════════════════


class TestFreeInputExecutorCrossCutting:
    """FreeInputExecutor 跨模式组件集成测试。"""

    def test_executor_with_custom_registry(self):
        """验证 executor 接受自定义 component_registry。"""
        from drama_engine.core.runtime.interactive_session.actions.free_input.executor import (
            FreeInputExecutor,
        )
        registry = FreeInputComponentRegistry()
        executor = FreeInputExecutor(component_registry=registry)
        assert executor._component_registry is registry

    def test_executor_default_registry(self):
        """验证 executor 默认使用内置 registry。"""
        from drama_engine.core.runtime.interactive_session.actions.free_input.executor import (
            FreeInputExecutor,
        )
        executor = FreeInputExecutor()
        registered = executor._component_registry.list_registered()
        assert "character_existence" in registered["input_guards"]
