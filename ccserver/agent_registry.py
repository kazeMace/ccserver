"""
agent_registry — 全局后台 Agent Handle 注册表。

spawn_background() 将 handle 注册到此模块，server.py 按 agent_id 查询并 cancel。
任务终结后自动注销。

事件流
────────────────────────────────────────────────────────────────────────────
spawn_background() → register_handle(handle)
                               ↓
                    server.py / cancel_agent_task()
                               ↓
                    get_handle(agent_id) → handle.cancel()
                               ↓
                    unregister_handle(agent_id)
"""

from loguru import logger


# 全局 handle 注册表：agent_id → BackgroundAgentHandle
_HANDLE_REGISTRY: dict[str, "BackgroundAgentHandle"] = {}


def register_handle(handle: "BackgroundAgentHandle") -> None:
    """
    将 BackgroundAgentHandle 注册到全局注册表。
    spawn_background() 调用它，使 server.py 可以按 agent_id 查找并 cancel。
    """
    _HANDLE_REGISTRY[handle.agent_id] = handle
    logger.debug(
        "AgentHandleRegistry: registered | agent_id={} agent_task_id={}",
        handle.agent_id[:8], handle.agent_task_id
    )


def get_handle(agent_id: str) -> "BackgroundAgentHandle | None":
    """根据 agent_id 查找已注册的 handle。"""
    return _HANDLE_REGISTRY.get(agent_id)


def unregister_handle(agent_id: str) -> None:
    """从注册表中移除 handle（任务终结后调用）。"""
    if agent_id in _HANDLE_REGISTRY:
        del _HANDLE_REGISTRY[agent_id]
        logger.debug("AgentHandleRegistry: unregistered | agent_id={}", agent_id[:8])


def list_handles() -> list["BackgroundAgentHandle"]:
    """返回所有已注册的 handle。"""
    return list(_HANDLE_REGISTRY.values())
