"""grow_flow 组件系统测试。

测试覆盖：
  1. GrowFlowState 生长状态追踪
  2. GrowFlowComponentRegistry 组件注册与解析
  3. GrowFlowPipeline 管道执行（使用 TemplateGenerator dry-run）
  4. GrowFlowStrategy 策略接口
  5. 各组件基本行为
"""

import pytest

from drama_engine.core.runtime.interactive_session.actions.free_input.grow_state import (
    GrowFlowState,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.grow_flow_registry import (
    GrowFlowComponentRegistry,
    build_default_grow_flow_registry,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.grow_flow_pipeline import (
    GrowFlowPipeline,
    GrowFlowStrategy,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.components.constraints import (
    EndingBoundConstraint,
    FreeConstraint,
    MaxRoundsConstraint,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.components.generators import (
    TemplateGrowFlowGenerator,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.components.interaction_modes import (
    BranchChoiceMode,
    ConfirmAdvanceMode,
    FreeInputOnlyMode,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.components.narration_styles import (
    DialogueSequenceStyle,
    MixedStyle,
    PlainNarrationStyle,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.components.presentations import (
    ChatFlowPresentation,
    CinematicPresentation,
    VisualNovelPresentation,
)


# ============================================================
# GrowFlowState 测试
# ============================================================


class TestGrowFlowState:
    """GrowFlowState 生长状态追踪测试。"""

    def test_init_creates_state_in_metadata(self):
        """初始化时在 metadata 中创建状态结构。"""
        metadata = {}
        state = GrowFlowState(metadata)
        assert "__grow_flow_state" in metadata
        assert state.total_count() == 0

    def test_init_preserves_existing_state(self):
        """已有状态时不覆盖。"""
        metadata = {
            "__grow_flow_state": {
                "total_count": 5,
                "depth_map": {"scene_a": 2},
            }
        }
        state = GrowFlowState(metadata)
        assert state.total_count() == 5
        assert state.depth_of("scene_a") == 2

    def test_register_increments_count(self):
        """register 增加 total_count。"""
        metadata = {}
        state = GrowFlowState(metadata)
        state.register("grow_001", "original_scene")
        assert state.total_count() == 1
        state.register("grow_002", "grow_001")
        assert state.total_count() == 2

    def test_register_tracks_depth(self):
        """register 正确追踪深度。"""
        metadata = {}
        state = GrowFlowState(metadata)
        # 原始场景深度 = 0
        assert state.depth_of("original") == 0
        # 第一层
        state.register("grow_1", "original")
        assert state.depth_of("grow_1") == 1
        # 第二层
        state.register("grow_2", "grow_1")
        assert state.depth_of("grow_2") == 2
        # 分叉：另一个从 grow_1 生长
        state.register("grow_3", "grow_1")
        assert state.depth_of("grow_3") == 2

    def test_should_force_ending_free_constraint(self):
        """free 类型永远不强制收束。"""
        metadata = {}
        state = GrowFlowState(metadata)
        for i in range(100):
            state.register(f"s_{i}", f"s_{i-1}" if i > 0 else "root")
        assert state.should_force_ending({"type": "free"}, "s_99") is False

    def test_should_force_ending_max_count(self):
        """达到 max_count 时强制收束。"""
        metadata = {}
        state = GrowFlowState(metadata)
        state.register("s_1", "root")
        state.register("s_2", "s_1")
        state.register("s_3", "s_2")
        config = {"type": "max_rounds", "max_count": 3}
        assert state.should_force_ending(config, "s_3") is True

    def test_should_force_ending_max_depth(self):
        """达到 max_depth 时强制收束。"""
        metadata = {}
        state = GrowFlowState(metadata)
        state.register("s_1", "root")
        state.register("s_2", "s_1")
        # s_2 depth=2，下一步 depth=3，max_depth=3 → 强制
        config = {"type": "ending_bound", "max_depth": 3}
        assert state.should_force_ending(config, "s_2") is True
        # 从 s_1 继续（depth=1 → next=2）不应强制
        assert state.should_force_ending(config, "s_1") is False

    def test_should_hint_ending(self):
        """hint_at_depth 触发提示。"""
        metadata = {}
        state = GrowFlowState(metadata)
        state.register("s_1", "root")
        state.register("s_2", "s_1")
        config = {"hint_at_depth": 2}
        # s_1 depth=1，next=2 >= hint_at=2 → True
        assert state.should_hint_ending(config, "s_1") is True
        # root depth=0，next=1 < 2 → False
        assert state.should_hint_ending(config, "root") is False

    def test_snapshot(self):
        """snapshot 返回状态副本。"""
        metadata = {}
        state = GrowFlowState(metadata)
        state.register("a", "root")
        state.register("b", "a")
        snap = state.snapshot()
        assert snap["total_count"] == 2
        assert snap["current_max_depth"] == 2
        # 修改 snapshot 不影响原始
        snap["total_count"] = 999
        assert state.total_count() == 2


# ============================================================
# GrowFlowComponentRegistry 测试
# ============================================================


class TestGrowFlowComponentRegistry:
    """组件注册表测试。"""

    def test_build_default_registry(self):
        """默认注册表包含所有内置组件。"""
        registry = build_default_grow_flow_registry()
        # 续写风格
        assert "plain_narration" in registry._narration_styles
        assert "dialogue_sequence" in registry._narration_styles
        assert "mixed" in registry._narration_styles
        # 互动方式
        assert "branch_choice" in registry._interaction_modes
        assert "free_input_only" in registry._interaction_modes
        assert "confirm_advance" in registry._interaction_modes
        # 约束
        assert "free" in registry._constraints
        assert "max_rounds" in registry._constraints
        assert "ending_bound" in registry._constraints
        # 展示
        assert "cinematic" in registry._presentations
        assert "chat_flow" in registry._presentations
        assert "visual_novel" in registry._presentations
        # 生成器
        assert "llm" in registry._generators
        assert "builtin" in registry._generators

    def test_resolve_pipeline_default_spec(self):
        """空 spec 使用默认组件解析出 Pipeline。"""
        registry = build_default_grow_flow_registry()
        spec = {"executor": "builtin"}
        pipeline = registry.resolve_pipeline(spec)
        assert isinstance(pipeline, GrowFlowPipeline)

    def test_resolve_pipeline_custom_spec(self):
        """自定义 spec 解析对应组件。"""
        registry = build_default_grow_flow_registry()
        spec = {
            "executor": "builtin",
            "narration_style": "dialogue_sequence",
            "interaction_mode": "free_input_only",
            "constraint": {"type": "max_rounds", "max_count": 5},
            "presentation": "cinematic",
        }
        pipeline = registry.resolve_pipeline(spec)
        assert isinstance(pipeline._narration, DialogueSequenceStyle)
        assert isinstance(pipeline._interaction, FreeInputOnlyMode)
        assert isinstance(pipeline._constraint, MaxRoundsConstraint)
        assert isinstance(pipeline._presentation, CinematicPresentation)
        assert isinstance(pipeline._generator, TemplateGrowFlowGenerator)

    def test_resolve_unknown_component_raises(self):
        """未注册的组件名应触发 AssertionError。"""
        registry = build_default_grow_flow_registry()
        spec = {"narration_style": "nonexistent_style", "executor": "builtin"}
        with pytest.raises(AssertionError, match="未注册的 narration_style"):
            registry.resolve_pipeline(spec)

    def test_register_custom_component(self):
        """GamePack 可注册自定义组件。"""
        registry = build_default_grow_flow_registry()

        class CustomNarration(PlainNarrationStyle):
            pass

        registry.register_narration_style("custom", CustomNarration)
        spec = {"narration_style": "custom", "executor": "builtin"}
        pipeline = registry.resolve_pipeline(spec)
        assert isinstance(pipeline._narration, CustomNarration)


# ============================================================
# 组件行为测试
# ============================================================


class TestNarrationStyles:
    """续写风格组件测试。"""

    def test_plain_narration_build_prompt(self):
        """PlainNarrationStyle 构造 prompt 包含格式说明。"""
        style = PlainNarrationStyle({})
        context = {
            "text": "我走向门口",
            "messages": [],
            "choices_instruction": "",
            "ending_ids": [],
            "depth": 1,
            "max_depth": 5,
            "total_count": 0,
            "max_count": 10,
        }
        system, user = style.build_prompt(context, hint=None)
        assert "narration" in system.lower() or "叙述" in system
        assert "我走向门口" in user

    def test_dialogue_sequence_build_prompt(self):
        """DialogueSequenceStyle 的 prompt 包含对话格式要求。"""
        style = DialogueSequenceStyle({})
        context = {
            "text": "你好",
            "messages": [],
            "choices_instruction": "",
            "ending_ids": [],
            "depth": 1,
            "max_depth": 0,
            "total_count": 0,
            "max_count": 0,
        }
        system, user = style.build_prompt(context, hint=None)
        assert "dialogue" in system.lower() or "对话" in system

    def test_plain_narration_parse_response(self):
        """解析 dict 响应。"""
        style = PlainNarrationStyle({})
        raw = {"narration": "房间安静下来。", "choices": []}
        result = style.parse_response(raw)
        assert result["narration"] == "房间安静下来。"

    def test_mixed_style_parse_response(self):
        """MixedStyle 保留 narration + dialogue_history。"""
        style = MixedStyle({})
        raw = {
            "narration": "旁白。",
            "dialogue_history": [{"speaker": "A", "text": "你好"}],
        }
        result = style.parse_response(raw)
        assert "narration" in result
        assert "dialogue_history" in result


class TestInteractionModes:
    """互动方式组件测试。"""

    def test_branch_choice_builds_choices(self):
        """BranchChoiceMode 生成包含 choices 的 controller_action。"""
        mode = BranchChoiceMode({})
        parsed = {
            "choices": [
                {"id": "opt_a", "text": "选项A"},
                {"id": "opt_b", "text": "选项B"},
            ]
        }
        spec = {"mode": "grow_flow"}
        action = mode.build_controller_action(parsed, spec)
        assert action["kind"] == "choice"
        assert len(action["choices"]) == 2

    def test_free_input_only_no_choices(self):
        """FreeInputOnlyMode 不生成 choices。"""
        mode = FreeInputOnlyMode({})
        parsed = {"narration": "继续"}
        spec = {}
        action = mode.build_controller_action(parsed, spec)
        assert action["kind"] == "free_text"
        assert "choices" not in action or action.get("choices") == []

    def test_confirm_advance_single_choice(self):
        """ConfirmAdvanceMode 生成单个 '继续' 选项。"""
        mode = ConfirmAdvanceMode({})
        parsed = {}
        spec = {}
        action = mode.build_controller_action(parsed, spec)
        assert action["kind"] == "choice"
        assert len(action["choices"]) == 1

    def test_branch_choice_choices_schema(self):
        """BranchChoiceMode 提供 choices schema 描述。"""
        mode = BranchChoiceMode({})
        desc = mode.choices_schema_description()
        assert desc  # 非空字符串


class TestConstraints:
    """剧情约束组件测试。"""

    @pytest.mark.asyncio
    async def test_free_constraint_always_allows(self):
        """FreeConstraint 永远允许继续。"""
        constraint = FreeConstraint({})
        state = GrowFlowState({})
        for i in range(50):
            state.register(f"s_{i}", f"s_{i-1}" if i > 0 else "root")
        result = await constraint.check(state, None)
        assert result is True

    @pytest.mark.asyncio
    async def test_max_rounds_blocks_at_limit(self):
        """MaxRoundsConstraint 到达上限时阻止。"""
        config = {"type": "max_rounds", "max_count": 3}
        constraint = MaxRoundsConstraint(config)
        state = GrowFlowState({})
        state.register("s_1", "root")
        state.register("s_2", "s_1")
        state.register("s_3", "s_2")

        class FakeCtx:
            current_scene_id = "s_3"

        result = await constraint.check(state, FakeCtx())
        assert result is False

    @pytest.mark.asyncio
    async def test_max_rounds_allows_below_limit(self):
        """MaxRoundsConstraint 未到上限时允许。"""
        config = {"type": "max_rounds", "max_count": 5}
        constraint = MaxRoundsConstraint(config)
        state = GrowFlowState({})
        state.register("s_1", "root")

        class FakeCtx:
            current_scene_id = "s_1"

        result = await constraint.check(state, FakeCtx())
        assert result is True


class TestPresentations:
    """交互展示组件测试。"""

    def test_chat_flow_builds_messages(self):
        """ChatFlowPresentation 生成 publication messages。"""
        pres = ChatFlowPresentation({})
        narration = {"narration": "天色渐暗。", "dialogue_history": []}
        action = {"kind": "choice", "choices": []}

        class FakeCtx:
            current_scene_id = "scene_01"

        patch = pres.build_scene_patch(narration, action, FakeCtx(), "grow_test_01")
        assert patch["type"] == "add_scene"
        assert patch["scene"]["id"] == "grow_test_01"
        assert len(patch["scene"]["publication"]["messages"]) >= 1
        assert patch["scene"]["publication"]["messages"][0]["content"]["text"] == "天色渐暗。"

    def test_cinematic_puts_dialogue_in_context(self):
        """CinematicPresentation 将 dialogue 放入 context。"""
        pres = CinematicPresentation({})
        narration = {
            "narration": "",
            "dialogue_history": [
                {"speaker": "A", "text": "你好"},
                {"speaker": "B", "text": "你好啊"},
            ],
        }
        action = {"kind": "choice", "choices": []}

        class FakeCtx:
            current_scene_id = "scene_02"

        patch = pres.build_scene_patch(narration, action, FakeCtx(), "grow_test_02")
        assert patch["scene"]["controller_action"]["kind"] == "cinematic"
        assert len(patch["scene"]["context"]["dialogue_history"]) == 2

    def test_visual_novel_single_message(self):
        """VisualNovelPresentation 生成单条 narration。"""
        pres = VisualNovelPresentation({})
        narration = {"narration": "故事开始了。"}
        action = {"kind": "choice", "choices": [{"id": "a", "text": "继续"}]}

        class FakeCtx:
            current_scene_id = "scene_03"

        patch = pres.build_scene_patch(narration, action, FakeCtx(), "grow_test_03")
        msgs = patch["scene"]["publication"]["messages"]
        assert len(msgs) == 1
        assert "故事开始了" in msgs[0]["content"]["text"]


# ============================================================
# GrowFlowPipeline 集成测试（dry-run）
# ============================================================


class TestGrowFlowPipeline:
    """Pipeline 集成测试，使用 TemplateGenerator。"""

    @pytest.mark.asyncio
    async def test_pipeline_execute_produces_patch(self):
        """Pipeline 执行返回合法 add_scene patch。"""
        registry = build_default_grow_flow_registry()
        spec = {
            "executor": "builtin",
            "narration_style": "plain_narration",
            "interaction_mode": "branch_choice",
            "constraint": {"type": "free"},
            "presentation": "chat_flow",
        }
        pipeline = registry.resolve_pipeline(spec)
        metadata = {}
        grow_state = GrowFlowState(metadata)

        class FakeCtx:
            current_scene_id = "origin_scene"
            session_metadata = metadata
            message_history = []

        result = await pipeline.execute(
            ctx=FakeCtx(),
            spec=spec,
            player_text="我推开了那扇门",
            grow_state=grow_state,
        )
        assert "patch" in result
        patch = result["patch"]
        assert patch["type"] == "add_scene"
        assert patch["scene"]["id"].startswith("grow_")
        assert "controller_action" in patch["scene"]

    @pytest.mark.asyncio
    async def test_pipeline_respects_max_rounds_constraint(self):
        """Pipeline 在达到 max_count 时返回 ending patch。"""
        registry = build_default_grow_flow_registry()
        spec = {
            "executor": "builtin",
            "narration_style": "plain_narration",
            "interaction_mode": "branch_choice",
            "constraint": {"type": "max_rounds", "max_count": 2},
            "presentation": "chat_flow",
        }
        pipeline = registry.resolve_pipeline(spec)
        metadata = {}
        grow_state = GrowFlowState(metadata)
        # 模拟已生成 2 个场景
        grow_state.register("s_1", "root")
        grow_state.register("s_2", "s_1")

        class FakeCtx:
            current_scene_id = "s_2"
            session_metadata = metadata
            message_history = []

        result = await pipeline.execute(
            ctx=FakeCtx(),
            spec=spec,
            player_text="继续",
            grow_state=grow_state,
        )
        # 收束时也返回 patch
        assert "patch" in result


# ============================================================
# GrowFlowStrategy 接口测试
# ============================================================


class TestGrowFlowStrategy:
    """GrowFlowStrategy 作为 FreeInputStrategy 的接口测试。"""

    @pytest.mark.asyncio
    async def test_strategy_execute(self):
        """GrowFlowStrategy.execute 返回 patch 结果。"""
        strategy = GrowFlowStrategy()
        metadata = {}
        grow_state = GrowFlowState(metadata)

        class FakeCtx:
            current_scene_id = "start"
            session_metadata = metadata
            message_history = []

        spec = {
            "executor": "builtin",
            "narration_style": "mixed",
            "interaction_mode": "confirm_advance",
            "constraint": {"type": "free"},
            "presentation": "visual_novel",
        }
        context = {
            "ctx": FakeCtx(),
            "text": "测试输入",
            "grow_state": grow_state,
        }
        result = await strategy.execute(mode="grow_flow", spec=spec, context=context)
        assert "patch" in result
        assert result["patch"]["type"] == "add_scene"


# ============================================================
# FreeInputStrategyRegistry 集成
# ============================================================


class TestRegistryIntegration:
    """验证 GrowFlowStrategy 已正确注册到 FreeInputStrategyRegistry。"""

    def test_grow_flow_registered(self):
        """grow_flow 模式注册的是 GrowFlowStrategy。"""
        from drama_engine.core.runtime.interactive_session.actions.free_input.registry import (
            FreeInputStrategyRegistry,
        )

        reg = FreeInputStrategyRegistry()
        strategy = reg.get("grow_flow")
        assert strategy is not None
        assert isinstance(strategy, GrowFlowStrategy)
