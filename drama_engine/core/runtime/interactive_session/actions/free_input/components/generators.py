"""生成器组件实现。

Generator 负责调用 LLM/模板生成原始内容。
与 prompt 模块解耦：Generator 只负责"调用"，不负责构造 prompt。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.executor import ExecutorRequest
from drama_engine.core.runtime.interactive_session.actions.free_input.components.base import (
    GrowFlowGenerator,
)

logger = logging.getLogger(__name__)


class LLMGrowFlowGenerator(GrowFlowGenerator):
    """LLM 生成器：通过 ExecutorRegistry 调用大模型。

    调用链：
      ctx.executor_registry.execute("llm", request)
      → LLMExecutor 获取/创建 Agent → 发送 prompt → 解析 JSON
    """

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        ctx: Any,
    ) -> dict[str, Any]:
        """调用 LLM 生成剧情内容。

        参数:
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            ctx: InteractiveExecutionContext

        返回:
            解析后的 LLM 响应 dict
        """
        # 通过 ExecutorRegistry 调用 LLM
        executor_registry = getattr(ctx, "executor_registry", None)
        if executor_registry is None or not executor_registry.has("llm"):
            logger.warning("[LLMGrowFlowGenerator] 无可用 executor_registry，fallback 到模板")
            return self._template_fallback()

        # 组合为完整 prompt（system + user）
        full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"
        logger.debug("[LLMGrowFlowGenerator] 调用 LLM，prompt 长度: %d", len(full_prompt))

        # 构造 executor 请求
        config: dict[str, Any] = {}
        if self._config.get("model"):
            config["model_name"] = self._config["model"]

        request = ExecutorRequest(
            purpose="grow_flow_generator",
            payload={"prompt": full_prompt},
            config=config,
        )

        response = await executor_registry.execute("llm", request)

        if not response.success:
            logger.warning("[LLMGrowFlowGenerator] LLM 调用失败: %s，fallback", response.error)
            return self._template_fallback()

        # 响应中已经是解析好的 dict（LLMExecutor 负责 JSON 解析）
        data = response.data
        # 如果返回的是 {"text": "..."} 格式，把 text 当 narration
        if "text" in data and "narration" not in data:
            data["narration"] = data.pop("text")
        return data

    def _template_fallback(self) -> dict[str, Any]:
        """LLM 不可用时的 fallback（返回固定结构）。"""
        return {
            "narration": "剧情继续向前推进。",
            "choices": [
                {"id": "continue", "text": "继续"},
            ],
            "should_end": False,
            "ending_id": None,
        }


class TemplateGrowFlowGenerator(GrowFlowGenerator):
    """模板生成器：不调 LLM，返回固定结构（dry-run 兜底）。"""

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        ctx: Any,
    ) -> dict[str, Any]:
        """返回固定模板结构（不调 LLM，dry-run 兜底）。"""
        text = "剧情继续向前推进。"
        return {
            "narration": text,
            "dialogue_history": [
                {"speaker": "narrator", "text": text},
            ],
            "choices": [
                {"id": "option_a", "text": "选项 A"},
                {"id": "option_b", "text": "选项 B"},
            ],
            "should_end": False,
            "ending_id": None,
        }


__all__ = ["LLMGrowFlowGenerator", "TemplateGrowFlowGenerator"]
