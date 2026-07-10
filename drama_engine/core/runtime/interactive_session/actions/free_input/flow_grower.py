"""Flow 生长器 — 动态生成 flow patch 添加新场景。

单一职责：处理 grow_flow 模式的完整逻辑，
包括 GrowFlowState 管理、patch 生成/验证/应用。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.strategy_resolver import (
    StrategyResolver,
)
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext
from drama_engine.core.runtime.interactive_session.patch.applier import FlowPatchApplier
from drama_engine.core.runtime.interactive_session.patch.validators import PatchValidator

logger = logging.getLogger(__name__)


class FlowGrower:
    """Flow 生长器。

    动态生成 flow patch 添加新场景到运行时 flow 中。

    流程：
      1. 初始化 GrowFlowState（生长状态追踪）
      2. 调用策略生成 patch
      3. 验证 + 应用 patch
      4. 设置 interactive_next_target 跳转到新场景
      5. 注册生长记录

    使用方式:
        grower = FlowGrower(strategy_resolver)
        result = await grower.grow(ctx, spec, controller_response)
    """

    def __init__(self, strategy_resolver: StrategyResolver) -> None:
        """初始化 Flow 生长器。

        参数:
            strategy_resolver: 策略解析器
        """
        self._resolver = strategy_resolver
        self._validator = PatchValidator()
        self._patch_applier = FlowPatchApplier()

    async def grow(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        controller_response: dict[str, Any],
    ) -> dict[str, Any]:
        """动态生成 flow patch 添加新场景。

        参数:
            ctx: 运行时上下文
            spec: DSL free_input 配置
            controller_response: 控制器响应

        返回:
            {"kind": "grow_flow", "flow_patch": {...}, "scene_id": str}

        异常:
            ValueError: patch 校验失败或策略未返回有效 patch
        """
        from drama_engine.core.runtime.interactive_session.actions.free_input.grow_state import (
            GrowFlowState,
        )

        # 初始化生长状态
        grow_state = GrowFlowState(ctx.session_metadata)

        # 获取策略并构造上下文
        strategy = await self._resolver.resolve("grow_flow", spec.get("generator"), ctx)

        context = {
            **ctx.full_context_payload(),
            "text": controller_response.get("text", ""),
            "ctx": ctx,
            "patch_journal": ctx.patch_journal,
            "grow_state": grow_state,
        }

        # 执行策略
        result = await strategy.execute("grow_flow", spec, context)

        patch = result.get("patch")
        if not isinstance(patch, dict):
            raise ValueError("grow_flow 需要策略返回 patch 字典")

        # 生成内容完整性自检：内容为空则不 apply，避免污染 flow
        parsed = result.get("parsed") or {}
        if not self._has_content(parsed):
            logger.warning("[FlowGrower] 生成内容为空，拒绝 apply")
            return {
                "kind": "guard_rejected",
                "phase": "generation",
                "reason": "生成内容为空：缺少 narration 和 dialogue_history",
            }

        # 验证并应用 patch
        self._validate_and_apply(ctx, patch, "grow_flow patch")
        record = ctx.patch_journal.append(
            "flow_patch", patch, {"scene": ctx.current_scene_id}
        )

        try:
            self._patch_applier.apply(ctx, patch)
        except Exception:
            self._rollback(ctx, record.patch_id)
            raise

        # 即时持久化 patch journal
        if ctx.on_persist is not None:
            ctx.on_persist()

        # 跳转到新生成的场景
        scene = patch.get("scene") or {}
        new_scene_id = str(scene.get("id") or scene.get("name") or "")
        if new_scene_id:
            ctx.session_metadata["interactive_next_target"] = new_scene_id
            grow_state.register(new_scene_id, ctx.current_scene_id)

        # 把解析后的生成内容暴露到 result 顶层，供 OutputGuard 校验
        parsed = result.get("parsed") or {}
        return {
            "kind": "grow_flow",
            "flow_patch": patch,
            "scene_id": new_scene_id,
            "narration": parsed.get("narration"),
            "dialogue_history": parsed.get("dialogue_history"),
            "choices": parsed.get("choices"),
        }

    def _has_content(self, parsed: dict[str, Any]) -> bool:
        """检查解析后的生成内容是否非空（至少有 narration 或 dialogue_history）。"""
        narration = parsed.get("narration")
        dialogue = parsed.get("dialogue_history")
        has_narration = bool(narration and str(narration).strip())
        has_dialogue = bool(dialogue and isinstance(dialogue, list) and len(dialogue) > 0)
        return has_narration or has_dialogue

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


__all__ = ["FlowGrower"]
