"""ccserver Agent 工厂 — 为 LLMExecutor 提供 Agent 创建和缓存。

InsideAgentFactory 负责：
  1. 从 session_metadata 获取已缓存的 Agent 实例
  2. 不存在时通过 ccserver AgentFactory 懒创建
  3. 缓存到 session_metadata 供后续复用

所有通过 ccserver 调用 LLM 的路径（LLMExecutor）都经由此工厂。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class InsideAgentFactory:
    """创建并缓存 ccserver Agent 实例。

    Agent 缓存在 session_metadata 中，key 为 METADATA_KEY。
    同一 session 内只创建一个 Agent 实例（单轮调用复用）。
    """

    METADATA_KEY = "__interactive_inside_agent"

    def get_or_create(self, metadata: dict[str, Any], spec: dict[str, Any]) -> Any | None:
        """获取已缓存的 Agent，或按 spec 懒创建。

        参数:
            metadata: session 级元数据（Agent 缓存在其中）
            spec: 创建配置
                - model: 模型名（可选）
                - api_key: API key（可选）
                - base_url: API base URL（可选）
                - system_prompt / system: 系统提示词（可选）
                - prompt_version: prompt 库版本（可选，默认 simple_agent:v0.0.1）
                - agent_id / name: Agent 名称标识（可选）
                - project_root: 项目根目录（可选）

        返回:
            ccserver Agent 实例，或 None（dry_run / 创建失败时）
        """
        assert isinstance(metadata, dict), "metadata 必须是 dict"

        # 优先返回缓存（包括显式注入的 agent）
        existing = (
            metadata.get(self.METADATA_KEY)
            or metadata.get("inside_agent")
            or metadata.get("llm_client")
            or metadata.get("llm_provider")
        )
        if existing is not None:
            return existing

        # dry_run 模式不创建 Agent
        if metadata.get("dry_run") is True or spec.get("dry_run") is True:
            return None

        # 尝试创建
        try:
            agent = self._create_agent(metadata, spec)
        except Exception as exc:  # noqa: BLE001
            metadata["__interactive_inside_agent_error"] = str(exc)
            return None

        # 缓存并返回
        metadata[self.METADATA_KEY] = agent
        return agent

    def _create_agent(self, metadata: dict[str, Any], spec: dict[str, Any]) -> Any:
        """通过 ccserver AgentFactory 创建 Agent 实例。"""
        from ccserver.emitters.collect import CollectEmitter
        from ccserver.factory import AgentFactory
        from ccserver.session import SessionManager

        project_root = spec.get("project_root") or metadata.get("project_root") or os.getcwd()
        session_manager = SessionManager(project_root=Path(str(project_root)))
        session = session_manager.create()
        emitter = CollectEmitter()

        system_prompt = str(
            spec.get("system")
            or spec.get("system_prompt")
            or "你是 interactive_session runtime 的内部裁判和剧情规划 Agent。"
        )

        kwargs: dict[str, Any] = {
            "name": str(spec.get("agent_id") or spec.get("name") or "interactive_inside_agent"),
            "system": system_prompt,
            "append_system": True,
            "run_mode": "auto",
            "stream": False,
        }

        if spec.get("model"):
            kwargs["model"] = str(spec["model"])
        if spec.get("api_key"):
            kwargs["api_key"] = str(spec["api_key"])
        if spec.get("base_url"):
            kwargs["base_url"] = str(spec["base_url"])

        # 默认用 simple_agent — 轻量 prompt 库，不加载 cc_reverse 身份栈
        kwargs["prompt_version"] = str(spec.get("prompt_version") or "simple_agent:v0.0.1")

        return AgentFactory.create_root(session, emitter, **kwargs)


__all__ = ["InsideAgentFactory"]
