"""
agent_bus — Session 级别的 Agent 间通信总线。

提供基于内存的异步消息传递机制，支持：
  - 向指定 agent 发送消息
  - 订阅/获取某个 agent 的邮箱（asyncio.Queue）
  - 广播消息到所有已注册 agent
"""

import asyncio
from loguru import logger


class SessionAgentBus:
    """
    绑定到一个 Session 的 Agent 通信总线。
    每个 agent 通过唯一的 agent_id 注册邮箱，其他 agent 可通过 bus 向其发送消息。
    """

    def __init__(self):
        # agent_id -> asyncio.Queue 映射
        self._mailboxes: dict[str, asyncio.Queue] = {}

    def register(self, agent_id: str) -> asyncio.Queue:
        """
        为指定 agent 注册一个邮箱，返回该邮箱队列。
        若已存在则返回已有的队列。
        """
        if agent_id not in self._mailboxes:
            self._mailboxes[agent_id] = asyncio.Queue()
            logger.debug("AgentBus: registered mailbox | agent_id={}", agent_id)
        return self._mailboxes[agent_id]

    def unregister(self, agent_id: str) -> None:
        """注销指定 agent 的邮箱。"""
        if agent_id in self._mailboxes:
            del self._mailboxes[agent_id]
            logger.debug("AgentBus: unregistered mailbox | agent_id={}", agent_id)

    def get_mailbox(self, agent_id: str) -> asyncio.Queue | None:
        """获取指定 agent 的邮箱，若未注册返回 None。"""
        return self._mailboxes.get(agent_id)

    async def send(self, to_agent_id: str, message: dict) -> bool:
        """
        向指定 agent 发送一条消息。
        返回 True 表示发送成功，False 表示目标 agent 未注册邮箱。
        """
        mailbox = self._mailboxes.get(to_agent_id)
        if mailbox is None:
            logger.warning("AgentBus: send failed | target={} not registered", to_agent_id)
            return False
        await mailbox.put(message)
        logger.debug("AgentBus: message sent | to={}", to_agent_id)
        return True

    async def broadcast(self, message: dict, exclude: str | None = None) -> None:
        """
        向所有已注册 agent 广播消息。
        exclude 可指定一个 agent_id 跳过（通常用于排除发送方自身）。
        """
        for agent_id, mailbox in self._mailboxes.items():
            if exclude and agent_id == exclude:
                continue
            await mailbox.put(message)
        logger.debug(
            "AgentBus: broadcast | count={} exclude={}",
            len(self._mailboxes), exclude
        )

    def list_agents(self) -> list[str]:
        """返回当前已注册邮箱的所有 agent_id 列表。"""
        return list(self._mailboxes.keys())
