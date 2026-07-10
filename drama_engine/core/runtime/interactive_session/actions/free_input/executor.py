"""自由输入执行器 — 主协调器。

单一职责：协调跨模式组件层的 6 个阶段 + 分发到具体模式执行器。
具体模式逻辑委托给 MapperExecutor / ContentGenerator / FlowGrower。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.content_generator import (
    ContentGenerator,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.models import (
    GuardResult,
    PlanResult,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.registry import (
    FreeInputComponentRegistry,
    build_default_free_input_component_registry,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.flow_grower import (
    FlowGrower,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.mapper_executor import (
    MapperExecutor,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.registry import (
    FreeInputStrategyRegistry,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.strategy_resolver import (
    StrategyResolver,
)
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext

logger = logging.getLogger(__name__)


class FreeInputExecutor:
    """自由输入执行器（主协调器）。

    职责：
      1. 协调跨模式组件层 6 个阶段
      2. 分发到具体模式执行器
      3. 保持对外接口不变

    组合：
      - StrategyResolver: 策略解析
      - MapperExecutor: choose_mapping 模式
      - ContentGenerator: branch_then_return / *_continue 模式
      - FlowGrower: grow_flow 模式
      - FreeInputComponentRegistry: 跨模式组件
    """

    def __init__(
        self,
        strategy_registry: FreeInputStrategyRegistry | None = None,
        component_registry: FreeInputComponentRegistry | None = None,
        plugin_registry=None,
        llm_client=None,
    ) -> None:
        """初始化执行器。

        参数:
            strategy_registry: 策略注册表（可选）
            component_registry: 跨模式组件注册表（可选）
            plugin_registry: 插件注册表（可选）
            llm_client: LLM 客户端（可选）
        """
        registry = strategy_registry or FreeInputStrategyRegistry()
        self._component_registry = component_registry or build_default_free_input_component_registry()

        # 组合子组件
        self._strategy_resolver = StrategyResolver(
            strategy_registry=registry,
            plugin_registry=plugin_registry,
            llm_client=llm_client,
        )
        self._mapper = MapperExecutor(self._strategy_resolver)
        self._content_generator = ContentGenerator(self._strategy_resolver, registry)
        self._flow_grower = FlowGrower(self._strategy_resolver)

    async def execute(
        self,
        ctx: InteractiveExecutionContext,
        mode: str,
        spec: dict[str, Any],
        controller_response: dict[str, Any],
    ) -> dict[str, Any]:
        """执行自由输入模式（含跨模式组件层）。

        完整流程：
          Phase 1: InputGuard chain — 前置校验
          Phase 2: Planner — 可选规划
          Phase 3: Mode dispatch — 分发到具体执行器
          Phase 4: OutputGuard chain — 后置校验
          Phase 5: ChoiceDesigner — 可选独立选项生成
          Phase 6: AssetResolver — 可选资产匹配

        参数:
            ctx: 运行时上下文
            mode: 策略模式（choose_mapping/branch_then_return/...）
            spec: DSL free_input 配置
            controller_response: 控制器响应

        返回:
            模式执行结果字典
        """
        assert mode, "free_input.mode 不能为空"
        player_text = str(controller_response.get("text", ""))
        guards_spec = spec.get("guards") or {}
        generation_spec = spec.get("generation") or {}

        logger.debug("[FreeInputExecutor] 执行模式: mode=%s", mode)

        # ═══ Phase 1: InputGuard chain ═══
        guard_result = await self._run_input_guards(player_text, mode, ctx, guards_spec)
        if guard_result:
            if not guard_result.passed:
                return {
                    "kind": "guard_rejected",
                    "phase": "input",
                    "reason": guard_result.reason,
                    "suggestions": guard_result.suggestions,
                }
            # 支持自动修正
            if guard_result.corrected_payload:
                player_text = str(guard_result.corrected_payload.get("text", player_text))
                controller_response = {**controller_response, "text": player_text}

        # ═══ Phase 2: Planner (可选) ═══
        planner_spec = generation_spec.get("planner") or spec.get("planner")
        plan = await self._run_planner(player_text, ctx, planner_spec)

        # ═══ Phase 3: Mode dispatch ═══
        result = await self._dispatch_mode(ctx, mode, spec, controller_response)

        # ═══ Phase 4: OutputGuard chain ═══
        output_guard_specs = list(
            (generation_spec.get("guards") or {}).get("output")
            or guards_spec.get("output")
            or []
        )
        if output_guard_specs and self._is_generation_mode(mode):
            result = await self._run_output_guards(
                result, mode, ctx, output_guard_specs, spec, controller_response
            )

        # ═══ Phase 5: ChoiceDesigner (可选) ═══
        # grow_flow 模式下若未显式配置 choice_designer，默认使用 llm_fallback 确保分支不断
        designer_spec = generation_spec.get("choice_designer") or spec.get("choice_designer")
        if not designer_spec and mode == "grow_flow":
            designer_spec = {"name": "llm_fallback", "config": {"count": 3}}
        if designer_spec and self._is_generation_mode(mode):
            result = await self._run_choice_designer(result, ctx, plan, designer_spec)

        # ═══ Phase 6: AssetResolver (可选) ═══
        # grow_flow 模式下若有素材池但未显式配置 resolver，默认使用 tag_matcher
        resolver_spec = generation_spec.get("asset_resolver") or spec.get("asset_resolver")
        if not resolver_spec and mode == "grow_flow":
            metadata = getattr(ctx, "session_metadata", {})
            if metadata.get("asset_pool"):
                resolver_spec = {"name": "tag_matcher", "config": {"max_results": 1}}
        if resolver_spec and self._is_generation_mode(mode):
            result = await self._run_asset_resolver(result, ctx, plan, resolver_spec)

        # ═══ Phase 7: 后置同步 ═══
        # Phase 5/6 修改了 flow_patch 中的 scene（choices/assets），
        # 但 grow_flow 在 Phase 3 已经 apply 过 patch（此时 materializer 做了 deepcopy）。
        # 需要用修改后的 patch 重新同步到运行时 script。
        if mode == "grow_flow" and result.get("flow_patch"):
            self._sync_patch_to_runtime(ctx, result["flow_patch"])

        return result

    async def continue_generated_beat(
        self,
        ctx: InteractiveExecutionContext,
        previous_result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """继续生成下一个节拍（多节拍续写）。

        参数:
            ctx: 运行时上下文
            previous_result: 上一次执行的返回值

        返回:
            下一个节拍结果，已到达上限则返回 None
        """
        return await self._content_generator.continue_beat(ctx, previous_result)

    # ---- Phase 实现 ----

    async def _run_input_guards(
        self,
        player_text: str,
        mode: str,
        ctx: InteractiveExecutionContext,
        guards_spec: dict[str, Any],
    ) -> GuardResult | None:
        """Phase 1: 执行 InputGuard chain。

        返回:
            最后一个 guard 的结果（含修正信息），全部通过返回 None
        """
        input_guard_specs = list(guards_spec.get("input") or [])
        if not input_guard_specs:
            return None

        input_guards = self._component_registry.resolve_input_guards(input_guard_specs)
        guard_payload = {
            "text": player_text,
            "mode": mode,
            "characters": self._extract_characters(ctx),
            "scene_id": getattr(ctx, "current_scene_id", ""),
            "message_history": getattr(ctx, "message_history", []),
        }
        last_result = None
        for guard in input_guards:
            result = await guard.check(guard_payload, ctx)
            if not result.passed:
                logger.info(
                    "[FreeInputExecutor] InputGuard 拒绝: guard=%s reason=%s",
                    guard.__class__.__name__, result.reason,
                )
                return result
            # 累积修正
            if result.corrected_payload:
                guard_payload["text"] = str(result.corrected_payload.get("text", guard_payload["text"]))
                last_result = result

        return last_result

    async def _run_planner(
        self,
        player_text: str,
        ctx: InteractiveExecutionContext,
        planner_spec: dict[str, Any] | None,
    ) -> PlanResult | None:
        """Phase 2: 执行 Planner。"""
        if not planner_spec:
            return None

        planner = self._component_registry.resolve_planner(planner_spec)
        if not planner:
            return None

        plan_context = {
            "message_history": getattr(ctx, "message_history", []),
            "characters": self._extract_characters(ctx),
        }
        plan = await planner.plan(player_text, plan_context, ctx)
        logger.debug(
            "[FreeInputExecutor] Planner 完成: title=%s characters=%s",
            plan.title, plan.characters_involved,
        )
        return plan

    async def _dispatch_mode(
        self,
        ctx: InteractiveExecutionContext,
        mode: str,
        spec: dict[str, Any],
        controller_response: dict[str, Any],
    ) -> dict[str, Any]:
        """Phase 3: 分发到具体模式执行器。"""
        if mode == "choose_mapping":
            return await self._mapper.execute(ctx, spec, controller_response)
        elif mode == "branch_then_return":
            return await self._content_generator.generate_branch(ctx, spec, controller_response)
        elif mode == "constrained_continue":
            return await self._content_generator.generate_beat(ctx, spec, controller_response, constrained=True)
        elif mode == "free_continue":
            return await self._content_generator.generate_beat(ctx, spec, controller_response, constrained=False)
        elif mode == "grow_flow":
            return await self._flow_grower.grow(ctx, spec, controller_response)
        else:
            raise ValueError(f"未知 free_input.mode: {mode}")

    async def _run_output_guards(
        self,
        result: dict[str, Any],
        mode: str,
        ctx: InteractiveExecutionContext,
        output_guard_specs: list[dict[str, Any]],
        spec: dict[str, Any],
        controller_response: dict[str, Any],
    ) -> dict[str, Any]:
        """Phase 4: 执行 OutputGuard chain。"""
        output_guards = self._component_registry.resolve_output_guards(output_guard_specs)
        output_payload = {**result, "_characters": self._extract_characters(ctx)}

        for guard in output_guards:
            check = await guard.check(output_payload, ctx)
            if not check.passed:
                logger.info(
                    "[FreeInputExecutor] OutputGuard 失败: guard=%s on_fail=%s reason=%s",
                    guard.__class__.__name__, guard.on_fail, check.reason,
                )
                if guard.on_fail == "skip":
                    continue
                elif guard.on_fail == "reject":
                    return {
                        "kind": "guard_rejected",
                        "phase": "output",
                        "reason": check.reason,
                    }
                elif guard.on_fail == "retry":
                    for _attempt in range(guard.max_retries):
                        result = await self._dispatch_mode(ctx, mode, spec, controller_response)
                        output_payload = {**result, "_characters": self._extract_characters(ctx)}
                        recheck = await guard.check(output_payload, ctx)
                        if recheck.passed:
                            break
                    else:
                        logger.warning("[FreeInputExecutor] OutputGuard retry 耗尽")
                elif guard.on_fail == "fallback":
                    result["_guard_fallback"] = True

        return result

    async def _run_choice_designer(
        self,
        result: dict[str, Any],
        ctx: InteractiveExecutionContext,
        plan: PlanResult | None,
        designer_spec: dict[str, Any],
    ) -> dict[str, Any]:
        """Phase 5: 执行 ChoiceDesigner。"""
        designer = self._component_registry.resolve_choice_designer(designer_spec)
        if not designer:
            return result

        design_context = {
            "message_history": getattr(ctx, "message_history", []),
            "characters": self._extract_characters(ctx),
            "plan": plan,
        }
        choices = await designer.design_choices(result, design_context, ctx)
        if choices:
            result = self._inject_choices(result, choices)
            logger.debug("[FreeInputExecutor] ChoiceDesigner 产出 %d 个选项", len(choices))

        return result

    async def _run_asset_resolver(
        self,
        result: dict[str, Any],
        ctx: InteractiveExecutionContext,
        plan: PlanResult | None,
        resolver_spec: dict[str, Any],
    ) -> dict[str, Any]:
        """Phase 6: 执行 AssetResolver。"""
        resolver = self._component_registry.resolve_asset_resolver(resolver_spec)
        if not resolver:
            return result

        asset_pool = self._get_asset_pool(ctx, resolver_spec)
        content_for_resolve = {
            "narration": result.get("narration", ""),
            "dialogue_history": result.get("dialogue_history", []),
            "title": plan.title if plan else "",
            "characters_involved": plan.characters_involved if plan else [],
            "asset_hints": plan.asset_hints if plan else {},
        }
        matches = await resolver.resolve(content_for_resolve, asset_pool, ctx)
        if matches:
            from dataclasses import asdict
            result["resolved_assets"] = [asdict(m) for m in matches]
            logger.debug("[FreeInputExecutor] AssetResolver 匹配 %d 个资产", len(matches))
            # 将最佳匹配的背景图写入 patch scene context.locations
            self._inject_asset_to_patch(result, matches)

        return result

    # ---- 工具方法 ----

    def _is_generation_mode(self, mode: str) -> bool:
        """判断是否为生成类模式。"""
        return mode in ("branch_then_return", "constrained_continue", "free_continue", "grow_flow")

    def _extract_characters(self, ctx: InteractiveExecutionContext) -> list[dict[str, Any]]:
        """从 ctx 提取角色列表。

        角色定义在顶层 roles: 块，编译后保存在 InteractiveScript.roles（list[dict]）。
        """
        script = getattr(ctx, "script", None)
        if script is None:
            return []
        roles = getattr(script, "roles", None)
        if isinstance(roles, list):
            return roles
        return []

    def _get_asset_pool(self, ctx: InteractiveExecutionContext, resolver_spec: dict[str, Any]) -> list[dict[str, Any]]:
        """从 ctx 获取资产池。"""
        metadata = getattr(ctx, "session_metadata", {})
        pool = metadata.get("asset_pool")
        if isinstance(pool, list):
            return pool
        return []

    def _inject_choices(self, result: dict[str, Any], choices: list[dict[str, Any]]) -> dict[str, Any]:
        """将 ChoiceDesigner 产出的 choices 注入结果。"""
        result["choices"] = choices
        patch = result.get("flow_patch")
        if isinstance(patch, dict):
            scene = patch.get("scene") or {}
            controller_action = scene.get("controller_action") or {}
            if controller_action:
                controller_action["choices"] = [
                    {"id": c.get("id", f"choice_{i}"), "text": c.get("text", "")}
                    for i, c in enumerate(choices)
                ]
        return result

    def _inject_asset_to_patch(self, result: dict[str, Any], matches: list[Any]) -> None:
        """将 AssetResolver 匹配的背景图写入 patch scene context.locations。

        前端通过 SCENE.current_location 读取背景图 URI 来渲染背景。
        只取 role=background 的第一个匹配项。
        """
        # 找第一个 background 类型的匹配
        bg_match = None
        for m in matches:
            if getattr(m, "role", "") == "background":
                bg_match = m
                break
        if bg_match is None and matches:
            bg_match = matches[0]
        if bg_match is None:
            return

        patch = result.get("flow_patch")
        if not isinstance(patch, dict):
            return
        scene = patch.get("scene")
        if not isinstance(scene, dict):
            return

        context = scene.setdefault("context", {})
        locations = context.setdefault("locations", [])
        # 写入 location 信息（含 image_url 供前端渲染）
        locations.clear()
        locations.append({
            "name": getattr(bg_match, "asset_id", ""),
            "image_url": getattr(bg_match, "path", ""),
        })

    def _sync_patch_to_runtime(self, ctx: InteractiveExecutionContext, patch: dict[str, Any]) -> None:
        """将 Phase 5/6 修改后的 patch 重新同步到运行时 script。

        grow_flow 在 Phase 3 apply 时 materializer 做了 deepcopy，
        后续 Phase 5（choices）和 Phase 6（assets）对 patch scene 的修改
        不会反映到 ctx.script.scenes 中。此方法直接更新编译后的 scene 属性。
        """
        scene = patch.get("scene")
        if not isinstance(scene, dict):
            return
        scene_id = str(scene.get("id") or "")
        if not scene_id:
            return
        if not hasattr(ctx.script, "scenes") or not isinstance(ctx.script.scenes, dict):
            return
        compiled_scene = ctx.script.scenes.get(scene_id)
        if compiled_scene is None:
            return

        # 同步 choices（Phase 5 注入）
        ca = scene.get("controller_action") or {}
        new_choices = ca.get("choices")
        if isinstance(new_choices, list) and hasattr(compiled_scene, "controller_action"):
            compiled_scene.controller_action.choices = list(new_choices)

        # 同步 context（Phase 6 注入了 locations）
        new_context = scene.get("context")
        if isinstance(new_context, dict):
            compiled_scene.context = dict(new_context)

        logger.debug("[FreeInputExecutor] 同步 patch 到运行时: scene=%s", scene_id)


__all__ = ["FreeInputExecutor"]
