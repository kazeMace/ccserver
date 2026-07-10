"""内容生成器 — 生成剧情内容（branch/beat）。

单一职责：处理 branch_then_return / constrained_continue / free_continue
三种模式的内容生成逻辑。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.strategy_resolver import (
    StrategyResolver,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.registry import (
    FreeInputStrategyRegistry,
)
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.patch.applier import FlowPatchApplier
from drama_engine.core.runtime.interactive_session.patch.validators import PatchValidator

logger = logging.getLogger(__name__)


class ContentGenerator:
    """内容生成器。

    负责三种生成模式的执行：
      - branch_then_return: 临时支线生成
      - constrained_continue: 约束到预设结局的节拍生成
      - free_continue: 自由续写节拍生成

    使用方式:
        generator = ContentGenerator(strategy_resolver, strategy_registry)
        result = await generator.generate_branch(ctx, spec, response)
        result = await generator.generate_beat(ctx, spec, response, constrained=True)
    """

    def __init__(
        self,
        strategy_resolver: StrategyResolver,
        strategy_registry: FreeInputStrategyRegistry | None = None,
    ) -> None:
        """初始化内容生成器。

        参数:
            strategy_resolver: 策略解析器
            strategy_registry: 内置策略注册表（ending_selector 需要）
        """
        self._resolver = strategy_resolver
        self._registry = strategy_registry or FreeInputStrategyRegistry()
        self._validator = PatchValidator()
        self._patch_applier = FlowPatchApplier()

    async def generate_branch(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        controller_response: dict[str, Any],
    ) -> dict[str, Any]:
        """Branch Then Return: 生成临时支线，执行后返回主线。

        流程：
          1. 调用内容生成策略生成支线剧情
          2. 生成 flow patch 添加临时场景
          3. 应用 patch 到运行时
          4. 记录返回点

        参数:
            ctx: 运行时上下文
            spec: DSL free_input 配置
            controller_response: 控制器响应

        返回:
            {"kind": "branch_then_return", "branch": {...}, "flow_patch": {...}}
        """
        strategy = await self._resolver.resolve(
            "branch_then_return", spec.get("generator"), ctx
        )

        context = {
            **ctx.full_context_payload(),
            "text": controller_response.get("text", ""),
            "return_to": spec.get("return_to", {}),
            "ctx": ctx,
        }

        # 执行策略生成内容
        generator_result = await strategy.execute("branch_then_return", spec, context)

        # 构造支线记录
        branch = {
            "type": "temporary_branch",
            "text": generator_result.get("text") or controller_response.get("text", ""),
            "beats": list(generator_result.get("beats", [])),
            "return_to": spec.get("return_to", {}),
        }

        # 生成 flow patch
        flow_patch = self._build_branch_patch(ctx, spec, generator_result, branch)
        branch_scene_id = self._extract_scene_id(flow_patch)
        if not branch_scene_id:
            raise ValueError("branch_then_return 需要 add_scene flow_patch")

        # 验证并应用 patch
        self._validate_and_apply(ctx, flow_patch, "branch flow_patch")
        branch_record = ctx.patch_journal.append(
            "branch_patch", branch, {"scene": ctx.current_scene_id}
        )
        flow_record = ctx.patch_journal.append(
            "flow_patch", flow_patch, {"scene": ctx.current_scene_id, "branch": True}
        )

        try:
            self._patch_applier.apply(ctx, flow_patch)
        except Exception:
            self._rollback(ctx, flow_record.patch_id)
            self._rollback(ctx, branch_record.patch_id)
            raise

        # 记录返回点
        return_to = spec.get("return_to", {})
        if return_to:
            ctx.session_metadata.setdefault("interactive_return_stack", []).append(return_to)
        ctx.session_metadata["interactive_next_target"] = branch_scene_id

        return {
            "kind": "branch_then_return",
            "branch": branch,
            "flow_patch": flow_patch,
        }

    async def generate_beat(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        controller_response: dict[str, Any],
        constrained: bool,
    ) -> dict[str, Any]:
        """生成剧情节拍（constrained_continue / free_continue）。

        参数:
            ctx: 运行时上下文
            spec: DSL free_input 配置
            controller_response: 控制器响应
            constrained: True=约束到预设结局，False=自由续写

        返回:
            {"kind": "constrained_continue"|"free_continue", "beat": {...}, "generation_state"?: {...}}
        """
        max_beats = int(spec.get("max_beats") or spec.get("max_turns") or 1)

        # 约束模式：先选择结局
        ending = None
        if constrained:
            ending = await self._resolve_ending(ctx, spec)

        # 生成第一个节拍
        beat = await self._generate_one_beat(
            ctx=ctx, spec=spec, controller_response=controller_response,
            constrained=constrained, ending=ending, beat_index=0,
        )

        result = {
            "kind": "constrained_continue" if constrained else "free_continue",
            "beat": beat,
        }

        # 如果需要多个节拍，记录生成状态
        if spec.get("loop", max_beats > 1) and max_beats > 1:
            result["generation_state"] = {
                "spec": dict(spec),
                "controller_response": dict(controller_response),
                "constrained": constrained,
                "ending": ending,
                "next_index": 1,
                "max_beats": max_beats,
            }

        return result

    async def continue_beat(
        self,
        ctx: InteractiveExecutionContext,
        previous_result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """继续生成下一个节拍（多节拍续写）。

        参数:
            ctx: 运行时上下文
            previous_result: 上一次 generate_beat 的返回值

        返回:
            下一个节拍的结果，如果已达到最大节拍数则返回 None
        """
        state = previous_result.get("generation_state")
        if not isinstance(state, dict):
            return None

        next_index = int(state.get("next_index", 0))
        max_beats = int(state.get("max_beats", 0))

        if next_index >= max_beats:
            return None

        spec = dict(state.get("spec", {}))
        controller_response = dict(state.get("controller_response", {}))
        constrained = bool(state.get("constrained"))

        beat = await self._generate_one_beat(
            ctx=ctx, spec=spec, controller_response=controller_response,
            constrained=constrained, ending=state.get("ending"), beat_index=next_index,
        )

        next_state = dict(state)
        next_state["next_index"] = next_index + 1

        result = {
            "kind": "constrained_continue" if constrained else "free_continue",
            "beat": beat,
        }

        if next_state["next_index"] < max_beats:
            result["generation_state"] = next_state

        return result

    # ---- 私有方法 ----

    async def _generate_one_beat(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        controller_response: dict[str, Any],
        constrained: bool,
        ending: Any,
        beat_index: int,
    ) -> dict[str, Any]:
        """生成一个剧情节拍。

        参数:
            ctx: 运行时上下文
            spec: DSL free_input 配置
            controller_response: 控制器响应
            constrained: 是否约束到结局
            ending: 目标结局（constrained=True 时有效）
            beat_index: 节拍索引（从 0 开始）

        返回:
            {"text": str, "beats": list, "index": int}
        """
        mode = "constrained_continue" if constrained else "free_continue"
        strategy = await self._resolver.resolve(mode, spec.get("generator"), ctx)

        context = {
            **ctx.full_context_payload(),
            "text": controller_response.get("text", ""),
            "constrained": constrained,
            "ending": ending,
            "beat_index": beat_index,
            "ctx": ctx,
        }

        # 执行策略
        result = await strategy.execute(mode, spec, context)

        # 构造节拍
        beat = {
            "text": result.get("text") or controller_response.get("text", ""),
            "beats": result.get("beats", []),
            "index": beat_index,
        }

        # 记录到 patch journal
        ctx.patch_journal.append(
            "generated_beat", beat,
            {"scene": ctx.current_scene_id, "constrained": constrained},
        )

        return beat

    async def _resolve_ending(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
    ) -> Any:
        """选择预设结局（constrained_continue 需要）。

        参数:
            ctx: 运行时上下文
            spec: DSL free_input 配置

        返回:
            选中的结局 id 或 name
        """
        ending_spec = spec.get("ending", {})
        if not isinstance(ending_spec, dict):
            return None

        selector_strategy = self._registry.get_ending_selector()

        context = {
            **ctx.full_context_payload(),
            "ctx": ctx,
            "state": ctx.state,
            "condition_evaluator": ctx.condition_evaluator,
        }

        result = await selector_strategy.execute(
            "ending_selection", {"ending": ending_spec}, context
        )
        return result.get("ending")

    def _build_branch_patch(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        generator_result: dict[str, Any],
        branch: dict[str, Any],
    ) -> dict[str, Any]:
        """构造 branch_then_return 的 flow patch。

        参数:
            ctx: 运行时上下文
            spec: DSL free_input 配置
            generator_result: 策略返回结果
            branch: 支线记录

        返回:
            flow patch 字典
        """
        # 如果策略或 DSL 显式指定了 patch，直接使用
        patch = (
            generator_result.get("patch")
            or generator_result.get("flow_patch")
            or spec.get("patch")
        )
        if isinstance(patch, dict):
            patch.setdefault("after", ctx.current_scene_id)
            return patch

        # 生成默认 add_scene patch
        scene_id = f"branch_{len(ctx.patch_journal.by_type('branch_patch')) + 1}"
        text = branch.get("text") or "支线剧情展开。"

        return {
            "type": "add_scene",
            "after": ctx.current_scene_id,
            "scene": {
                "id": scene_id,
                "type": "scene",
                "scope": {"id": "story", "visibility": "public"},
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {"kind": "none", "response": {"mode": "none"}},
                "controller_action": {"enabled": False, "kind": "none"},
                "publication": {
                    "messages": [
                        {
                            "audience": {"scope": "story"},
                            "content": {"text": text},
                        }
                    ]
                },
            },
        }

    def _extract_scene_id(self, patch: dict[str, Any]) -> str:
        """从 flow patch 提取新场景 id。"""
        if patch.get("type") != "add_scene":
            return ""
        scene = patch.get("scene")
        if not isinstance(scene, dict):
            return ""
        return str(scene.get("id") or scene.get("name") or "")

    def _validate_and_apply(
        self,
        ctx: InteractiveExecutionContext,
        patch: dict[str, Any],
        label: str,
    ) -> None:
        """验证 flow patch。"""
        errors = self._validator.validate_flow_patch(patch, ctx.script)
        if errors:
            raise ValueError(f"{label} 校验失败: {errors}")
        self._patch_applier.preview(ctx, patch)

    def _rollback(self, ctx: InteractiveExecutionContext, patch_id: str) -> None:
        """回滚 patch journal 记录。"""
        removed = ctx.patch_journal.rollback_last()
        assert removed is not None and removed.patch_id == patch_id, (
            f"patch journal 回滚顺序错误，期望 {patch_id}，实际 {removed.patch_id if removed else None}"
        )


__all__ = ["ContentGenerator"]
