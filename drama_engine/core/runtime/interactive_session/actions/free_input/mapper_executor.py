"""输入映射执行器 — 把自由文本映射到固定选项。

单一职责：处理 choose_mapping 模式的完整逻辑，
包括策略调用、结果匹配、fallback 处理。
"""

from __future__ import annotations

import logging
import random as _random
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.strategy_resolver import (
    StrategyResolver,
)
from drama_engine.core.runtime.interactive_session.context import InteractiveExecutionContext

logger = logging.getLogger(__name__)


class MapperExecutor:
    """输入映射执行器。

    把玩家自由输入映射到预制选项。

    映射失败时按 fallback 策略处理：
      - random: 随机选一个（默认）
      - closest: 取第一个（置信度最低也接受）
      - reject: 拒绝，要求重新输入
      - generate: 进入生成流程

    使用方式:
        mapper = MapperExecutor(strategy_resolver)
        result = await mapper.execute(ctx, spec, controller_response)
    """

    def __init__(self, strategy_resolver: StrategyResolver) -> None:
        """初始化映射执行器。

        参数:
            strategy_resolver: 策略解析器（用于获取 mapper 策略实例）
        """
        self._resolver = strategy_resolver

    async def execute(
        self,
        ctx: InteractiveExecutionContext,
        spec: dict[str, Any],
        controller_response: dict[str, Any],
    ) -> dict[str, Any]:
        """执行输入映射。

        参数:
            ctx: 运行时上下文
            spec: DSL free_input 配置（包含 choices、mapper 等）
            controller_response: 控制器响应（包含玩家输入的 text）

        返回:
            {
                "kind": "choose_mapping",
                "selected_choice": 选中的选项 id,
                "to": 跳转目标,
                "text": 玩家输入,
                "confidence": 匹配置信度
            }
            或 fallback=reject 时:
            {
                "kind": "guard_rejected",
                "phase": "mapper",
                "reason": str,
                "suggestions": list
            }
        """
        choices = list(spec.get("choices", []))
        mapper_spec = spec.get("mapper") or {}
        fallback = str(mapper_spec.get("fallback", "random"))

        # 获取映射策略实例
        strategy = await self._resolver.resolve("choose_mapping", mapper_spec, ctx)

        # 构造策略执行上下文
        context = {
            **ctx.full_context_payload(),
            "text": controller_response.get("text", ""),
            "choices": choices,
        }

        # 执行策略
        result = await strategy.execute("choose_mapping", spec, context)

        # 提取选中的选项
        selected_id = result.get("selected_choice") or result.get("choice_id")
        selected = self._choice_by_id(choices, selected_id)

        # 映射失败时按 fallback 策略处理
        if not selected and choices:
            selected = await self._handle_fallback(fallback, choices, spec, ctx, controller_response)
            if isinstance(selected, dict) and selected.get("kind") == "guard_rejected":
                return selected
            logger.info(
                "[MapperExecutor] 映射失败, fallback=%s, selected=%s",
                fallback, selected.get("id") if selected else None,
            )

        if not selected:
            selected = {}

        return {
            "kind": "choose_mapping",
            "selected_choice": selected.get("id"),
            "to": selected.get("to"),
            "text": controller_response.get("text", ""),
            "confidence": result.get("confidence"),
        }

    async def _handle_fallback(
        self,
        fallback: str,
        choices: list[dict[str, Any]],
        spec: dict[str, Any],
        ctx: InteractiveExecutionContext,
        controller_response: dict[str, Any],
    ) -> dict[str, Any]:
        """处理映射失败的 fallback 策略。

        参数:
            fallback: 策略名称（random/closest/reject/generate）
            choices: 可选项列表
            spec: DSL 配置
            ctx: 运行时上下文
            controller_response: 控制器响应

        返回:
            选中的 choice 字典，或 reject 时返回 guard_rejected 结构
        """
        if fallback == "random":
            return _random.choice(choices)
        elif fallback == "closest":
            return choices[0]
        elif fallback == "reject":
            return {
                "kind": "guard_rejected",
                "phase": "mapper",
                "reason": "无法将输入映射到任何选项，请重新选择",
                "suggestions": [c.get("text", "") for c in choices[:3]],
            }
        elif fallback == "generate":
            # generate fallback 返回 None，由调用方处理
            # 这里返回一个标记，让上层知道需要进入生成流程
            return {
                "kind": "_fallback_generate",
            }
        else:
            return _random.choice(choices)

    def _choice_by_id(
        self,
        choices: list[dict[str, Any]],
        choice_id: Any,
    ) -> dict[str, Any]:
        """根据 id 查找选项。

        参数:
            choices: 选项列表
            choice_id: 选项 id

        返回:
            匹配的选项字典，如果未找到则返回空字典
        """
        if choice_id is None:
            return {}
        for choice in choices:
            if str(choice.get("id")) == str(choice_id):
                return choice
        return {}


__all__ = ["MapperExecutor"]
