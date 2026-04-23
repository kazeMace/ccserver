"""
agent_scheduler — Session 级别的后台 Agent 调度器。

管理所有后台 Agent 的生命周期：启动、查询、列出、取消。
"""

import asyncio
from typing import Optional

from loguru import logger

from .agent_handle import BackgroundAgentHandle


class AgentScheduler:
    """
    Session 级别的后台 Agent 调度器。

    使用方式：
        1. 创建根 Agent 后，调用 scheduler.set_parent(agent) 绑定父 Agent
        2. 通过 scheduler.spawn(prompt, ...) 启动后台 Agent
    """

    def __init__(self, session: "Session"):
        self.session = session
        self._handles: dict[str, BackgroundAgentHandle] = {}
        self._parent_agent: Optional["Agent"] = None

    def set_parent(self, agent: "Agent") -> None:
        """
        设置父 Agent。

        后台 Agent 需要通过父 Agent 的 spawn_background() 能力创建，
        因此在使用 spawn 前必须先设置 parent agent。
        """
        self._parent_agent = agent

    def spawn(
        self,
        prompt: str,
        agent_def=None,
        agent_name: str = None,
        task_id: str = None,
    ) -> BackgroundAgentHandle:
        """启动后台 Agent（非阻塞）。"""
        if self._parent_agent is None:
            raise RuntimeError(
                "AgentScheduler: parent agent not set. "
                "Call set_parent() before spawn()."
            )
        handle = self._parent_agent.spawn_background(
            prompt=prompt,
            agent_def=agent_def,
            agent_name=agent_name,
            task_id=task_id,
        )
        self._handles[handle.agent_id] = handle
        logger.info(
            "Background agent spawned | agent_id={} task_id={}",
            handle.agent_id[:8],
            task_id,
        )
        return handle

    def get(self, agent_id: str) -> Optional[BackgroundAgentHandle]:
        """查询后台 Agent 状态。"""
        return self._handles.get(agent_id)

    def list_all(self) -> list[BackgroundAgentHandle]:
        """列出所有后台 Agent。"""
        return list(self._handles.values())

    def cancel(self, agent_id: str) -> bool:
        """取消后台 Agent。"""
        handle = self._handles.get(agent_id)
        if handle:
            asyncio.create_task(handle.cancel())
            logger.info("Background agent cancelling | agent_id={}", agent_id[:8])
            return True
        logger.warning("Background agent not found for cancel | agent_id={}", agent_id[:8])
        return False
