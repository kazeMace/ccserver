"""生成器组件实现。

Generator 负责调用 LLM/模板生成原始内容。
与 prompt 模块解耦：Generator 只负责"调用"，不负责构造 prompt。
"""

from __future__ import annotations

import json
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
        if not isinstance(data, dict):
            logger.warning("[LLMGrowFlowGenerator] LLM 返回非 dict: %s，fallback", type(data))
            return self._template_fallback()

        # 如果返回的是 {"text": "..."} 格式，把 text 当 narration
        if "text" in data and "narration" not in data:
            data["narration"] = data.pop("text")

        # 二次提取：当 LLMExecutor 解析失败，data 只有 narration（由 text rename 而来），
        # 且内容包含 JSON 结构时，尝试从中提取真正的结构化数据
        if "narration" in data and "dialogue_history" not in data:
            narration_text = str(data.get("narration", ""))
            re_extracted = self._try_extract_json_from_text(narration_text)
            if re_extracted is not None:
                logger.info("[LLMGrowFlowGenerator] 二次提取成功，覆盖 fallback 数据")
                data = re_extracted

        # 内容为空时记录原始响应并 fallback，避免下游 guard 拒绝
        has_narration = bool(data.get("narration") and str(data.get("narration")).strip())
        has_dialogue = bool(data.get("dialogue_history"))
        if not has_narration and not has_dialogue:
            logger.warning(
                "[LLMGrowFlowGenerator] LLM 返回内容为空，keys=%s raw=%r，fallback",
                list(data.keys()), response.raw,
            )
            return self._template_fallback()

        return data

    def _template_fallback(self) -> dict[str, Any]:
        """LLM 不可用时的 fallback（返回固定结构）。

        同时提供 narration 和 dialogue_history，兼容各 narration_style，
        避免因缺字段被 SchemaConformanceGuard 拒绝。
        """
        text = "剧情继续向前推进。"
        return {
            "narration": text,
            "dialogue_history": [
                {"speaker": "narrator", "text": text},
            ],
            "choices": [
                {"id": "continue", "text": "继续"},
            ],
            "should_end": False,
            "ending_id": None,
        }

    def _try_extract_json_from_text(self, text: str) -> dict[str, Any] | None:
        """从文本中提取第一个包含 dialogue_history 或 narration 的 JSON 对象。

        当 LLMExecutor._parse_text 首次解析失败（如 LLM 在 JSON 前后添加了说明文字），
        整个响应被作为纯文本 fallback。此方法在 Generator 层进行二次尝试，
        使用 raw_decode 从中提取有效的 JSON 结构。
        """
        # 先确认文本中是否可能包含 JSON
        if '{' not in text:
            return None

        decoder = json.JSONDecoder()
        pos = 0
        while pos < len(text):
            brace = text.find('{', pos)
            if brace == -1:
                break
            try:
                result, end_idx = decoder.raw_decode(text, brace)
                if isinstance(result, dict):
                    # 验证提取到的是有意义的剧情结构
                    if result.get("dialogue_history") or result.get("narration"):
                        return result
            except (json.JSONDecodeError, ValueError):
                pass
            pos = brace + 1

        return None


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
