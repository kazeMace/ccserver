"""LLM 执行器 — 通过 ccserver Agent 进行单轮模型请求。

职责：获取/创建 Agent → 发送 prompt → 解析 JSON → 返回。
不关心 prompt 内容是什么（由上层功能组件构造）。

DSL 可配参数（全部可选，不填从 ccserver/环境变量取）:
    executor: llm
    model_name: "..."
    api_key: "..."
    base_url: "..."
    prompt_version: "..."   # 默认 simple_agent:v0.0.1
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any

from drama_engine.core.executor.base import BaseExecutor, ExecutorRequest, ExecutorResponse

logger = logging.getLogger(__name__)


class LLMExecutor(BaseExecutor):
    """LLM 执行器 — ccserver Agent 单轮请求。

    内部使用 InsideAgentFactory 管理 Agent 的创建和缓存。
    Agent 缓存在 session_metadata 中，同一 session 复用同一 Agent 实例。
    """

    def __init__(self, session_metadata: dict[str, Any]) -> None:
        """初始化 LLM 执行器。

        参数:
            session_metadata: session 级元数据（Agent 缓存在其中）
        """
        assert isinstance(session_metadata, dict), "session_metadata 必须是 dict"
        self._metadata = session_metadata
        from drama_engine.core.executor.agent_factory import InsideAgentFactory
        self._factory = InsideAgentFactory()

    async def execute(self, request: ExecutorRequest) -> ExecutorResponse:
        """执行 LLM 请求。

        request.payload 必须包含 "prompt" 字段（完整 prompt 文本）。
        request.config 可包含 model_name/api_key/base_url/prompt_version。
        """
        prompt = request.payload.get("prompt")
        assert prompt, "LLMExecutor 要求 payload 中包含 prompt 字段"

        # 构造 agent 创建 spec
        spec = self._build_agent_spec(request.config)

        # 获取或创建 agent
        agent = self._factory.get_or_create(self._metadata, spec)
        if agent is None:
            logger.warning("[LLMExecutor] 无法创建 Agent，返回失败")
            return ExecutorResponse(
                success=False,
                error="无法创建 LLM Agent（可能处于 dry_run 模式）",
            )

        # 调用 agent
        try:
            raw_response = await self._call_agent(agent, str(prompt))
        except Exception as exc:
            logger.error("[LLMExecutor] Agent 调用失败: %s", exc)
            return ExecutorResponse(success=False, error=str(exc), raw=exc)

        # 解析响应
        data = self._parse_response(raw_response)
        logger.debug("[LLMExecutor] 调用成功，purpose=%s", request.purpose)
        return ExecutorResponse(success=True, data=data, raw=raw_response)

    def _build_agent_spec(self, config: dict[str, Any]) -> dict[str, Any]:
        """从 executor config 构造 InsideAgentFactory 所需的 spec。"""
        spec: dict[str, Any] = {}
        if config.get("model_name"):
            spec["model"] = config["model_name"]
        if config.get("api_key"):
            spec["api_key"] = config["api_key"]
        if config.get("base_url"):
            spec["base_url"] = config["base_url"]
        if config.get("prompt_version"):
            spec["prompt_version"] = config["prompt_version"]
        if config.get("system_prompt"):
            spec["system_prompt"] = config["system_prompt"]
        return spec

    async def _call_agent(self, agent: Any, prompt: str) -> Any:
        """调用 agent（兼容多种接口）。"""
        if hasattr(agent, "run"):
            value = agent.run(prompt)
        elif hasattr(agent, "act"):
            value = agent.act(prompt, None)
        elif hasattr(agent, "complete"):
            value = agent.complete(prompt)
        elif callable(agent):
            value = agent({"prompt": prompt})
        else:
            raise TypeError(f"不支持的 Agent 类型: {type(agent)}")

        if inspect.isawaitable(value):
            value = await value
        return value

    def _parse_response(self, raw: Any) -> dict[str, Any]:
        """解析 Agent 响应为 dict。"""
        # ccserver Agent 返回 {text, data} 结构
        if isinstance(raw, dict):
            if "text" in raw and "data" in raw:
                data = raw.get("data")
                if isinstance(data, dict):
                    return data
                return self._parse_text(str(raw.get("text") or ""))
            return raw

        # 字符串：尝试 JSON 解析
        if isinstance(raw, str):
            return self._parse_text(raw)

        logger.warning("[LLMExecutor] 未知响应类型: %s", type(raw))
        return {"text": str(raw)}

    def _parse_text(self, text: str) -> dict[str, Any]:
        """解析文本为 JSON dict。"""
        text = text.strip()
        # 去除 markdown 代码块标记
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

        # fallback：把文本当纯文本返回
        return {"text": text}


__all__ = ["LLMExecutor"]
