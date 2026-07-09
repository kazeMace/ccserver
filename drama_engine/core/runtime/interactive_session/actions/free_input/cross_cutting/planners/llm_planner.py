"""LLM 规划器基类 + TheClause 内置实现。

LLMPlanner: 基于 LLM executor 的 Planner 基类。
    子类只需实现 build_prompt() 来组装提示词。

TheClausePlanner: the_clause 专用规划器（系统内置），
    注册名 "the_clause_planner"，内部走 llm executor。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.executor import ExecutorRequest
from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.base import Planner
from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.models import PlanResult
from drama_engine.core.runtime.interactive_session.actions.free_input.prompts.planner import (
    PLANNER_SYSTEM_PROMPT,
    PLANNER_USER_TEMPLATE,
)

logger = logging.getLogger(__name__)


class LLMPlanner(Planner):
    """基于 LLM 的 Planner 基类。

    子类只需实现 build_prompt() 来组装提示词。
    调用流程：build_prompt() → executor("llm") → parse_result()
    """

    async def plan(
        self,
        player_action: str,
        context: dict[str, Any],
        ctx: Any,
    ) -> PlanResult:
        """通过 LLM 生成剧情规划。

        参数:
            player_action: 玩家行动描述
            context: 当前剧情上下文
            ctx: InteractiveExecutionContext
        """
        # 获取 executor_registry
        executor_registry = getattr(ctx, "executor_registry", None)
        if executor_registry is None or not executor_registry.has("llm"):
            logger.warning("[LLMPlanner] 无可用 executor_registry，返回空规划")
            return PlanResult()

        # 子类组装 prompt
        prompt = self.build_prompt(player_action, context, ctx)

        # 通过 executor 调用 LLM
        request = ExecutorRequest(
            purpose="planner",
            payload={"prompt": prompt},
            config=self._executor_config(),
        )
        response = await executor_registry.execute("llm", request)

        if not response.success:
            logger.warning("[LLMPlanner] LLM 调用失败: %s，返回空规划", response.error)
            return PlanResult()

        # 解析结果
        return self._parse_result(response.data)

    def build_prompt(
        self,
        player_action: str,
        context: dict[str, Any],
        ctx: Any,
    ) -> str:
        """组装完整 prompt。子类必须实现。

        参数:
            player_action: 玩家行动
            context: 剧情上下文
            ctx: InteractiveExecutionContext

        返回:
            完整的 prompt 文本（system + user 拼接）
        """
        raise NotImplementedError("子类必须实现 build_prompt()")

    def _executor_config(self) -> dict[str, Any]:
        """从组件 config 中提取 executor 级参数。"""
        config: dict[str, Any] = {}
        if self._config.get("model_name"):
            config["model_name"] = self._config["model_name"]
        if self._config.get("api_key"):
            config["api_key"] = self._config["api_key"]
        if self._config.get("base_url"):
            config["base_url"] = self._config["base_url"]
        if self._config.get("system_prompt"):
            config["system_prompt"] = self._config["system_prompt"]
        return config

    def _parse_result(self, data: dict[str, Any]) -> PlanResult:
        """解析 LLM 返回的 JSON 为 PlanResult。"""
        return PlanResult(
            title=str(data.get("title") or ""),
            synopsis=str(data.get("synopsis") or ""),
            characters_involved=list(data.get("characters_involved") or []),
            outline=list(data.get("outline") or []),
            asset_hints=dict(data.get("asset_hints") or {}),
            metadata=dict(data.get("metadata") or {}),
        )
