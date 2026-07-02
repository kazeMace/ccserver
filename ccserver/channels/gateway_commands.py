"""
channels/gateway_commands — Gateway 层内置控制命令处理器。

这些命令在消息进入 Agent 之前被拦截：
  /stop    中断当前运行的 Agent
  /new     清空历史，开启新对话（/reset 为别名）
  /reset   同 /new
  /status  返回当前 Agent 运行状态
  /help    列出所有可用命令

特点：执行后直接向用户回复，不启动新 Agent，不进入 session.messages 上下文。
"""

from dataclasses import dataclass
from typing import Optional

from loguru import logger


# Gateway 层命令名称集合（小写，不含 /）
GATEWAY_COMMANDS: frozenset[str] = frozenset({"stop", "new", "reset", "status", "help"})


@dataclass
class GatewayCommandResult:
    """
    控制命令的执行结果。

    Attributes:
        handled:  True 表示命令已被处理，调用方不再走 Agent 路径
        reply:    回复给用户的文本（None 表示静默执行，不回复）
    """
    handled: bool
    reply: Optional[str] = None


class GatewayCommandHandler:
    """
    Gateway 层控制命令处理器。

    每个 ChannelGateway（或 InboundRouter）持有一个实例。

    Args:
        session_manager: SessionManager 实例，用于查找 Session
        lifecycle:       ChannelLifecycle 实例，用于 /status 显示 channel 状态
        runner:          AgentRunner 实例，/new 命令时让 LRU 缓存失效
    """

    def __init__(self, session_manager, lifecycle, runner=None):
        self._session_manager = session_manager
        self._lifecycle = lifecycle
        self._runner = runner  # 可选：P2-3 Agent LRU 缓存失效

    def is_gateway_command(self, text: str) -> bool:
        """
        判断消息文本是否是 Gateway 层命令。

        Args:
            text: 用户消息原文

        Returns:
            True 表示是 Gateway 层命令，需要拦截处理
        """
        if not text or not text.startswith("/"):
            return False
        name = text[1:].split(maxsplit=1)[0].lower()
        return name in GATEWAY_COMMANDS

    async def handle(self, text: str, session_key: str) -> GatewayCommandResult:
        """
        处理 Gateway 层命令。

        Args:
            text:        用户输入的原始消息（以 / 开头）
            session_key: 当前 session key

        Returns:
            GatewayCommandResult。handled=True 时调用方跳过 Agent 路径。
        """
        parts = text[1:].split(maxsplit=1)
        name = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""

        if name == "stop":
            return await self._handle_stop(session_key)
        elif name in ("new", "reset"):
            return await self._handle_new(session_key, args)
        elif name == "status":
            return await self._handle_status(session_key)
        elif name == "help":
            return await self._handle_help(session_key)

        return GatewayCommandResult(handled=False)

    async def _handle_stop(self, session_key: str) -> GatewayCommandResult:
        """中断当前运行的 Agent。"""
        session = self._session_manager.get(session_key)
        if session and session.root_agent:
            session.root_agent.interrupt()
            logger.info("Gateway /stop: agent interrupted | session={}", session_key[:8])
            return GatewayCommandResult(handled=True, reply="已中断当前任务。")
        return GatewayCommandResult(handled=True, reply="当前没有正在运行的任务。")

    async def _handle_new(self, session_key: str, args: str) -> GatewayCommandResult:
        """清空会话历史，开启新对话。"""
        session = self._session_manager.get(session_key)
        if session:
            # 先中断正在运行的 Agent
            if session.root_agent:
                session.root_agent.interrupt()
            # 清空消息历史（内存 + 持久化）
            session.rewrite_messages([])
            # P2-3：/new 后让 Agent LRU 缓存失效，确保下次创建全新 Agent
            if self._runner is not None and hasattr(self._runner, "invalidate_agent"):
                self._runner.invalidate_agent(session.id)
            logger.info("Gateway /new: session cleared | session={}", session_key[:8])

        title = args or "新对话"
        return GatewayCommandResult(handled=True, reply=f"已开启新对话：{title}")

    async def _handle_status(self, session_key: str) -> GatewayCommandResult:
        """返回当前 Agent 运行状态和 channel 信息。"""
        session = self._session_manager.get(session_key)
        if not session:
            return GatewayCommandResult(handled=True, reply="无活跃会话。")

        agent = session.root_agent
        if agent is None:
            agent_status = "空闲"
        else:
            phase = getattr(getattr(agent, "state", None), "phase", "unknown")
            round_num = getattr(getattr(agent, "state", None), "round_num", 0)
            agent_status = f"运行中（{phase}，第 {round_num} 轮）"

        channels = self._lifecycle.list_running()
        channel_info = (
            "、".join(f"{c['channel_id']}:{c['account_id']}" for c in channels)
            or "无"
        )

        reply = f"Agent 状态：{agent_status}\n活跃 Channel：{channel_info}"
        return GatewayCommandResult(handled=True, reply=reply)

    async def _handle_help(self, session_key: str) -> GatewayCommandResult:
        """列出所有可用命令（Gateway 层 + Agent 层）。"""
        gateway_lines = [
            "**Gateway 控制命令（立即执行，不进入 AI 上下文）：**",
            "  /stop    — 中断当前运行的 Agent",
            "  /new     — 清空历史，开启新对话",
            "  /reset   — 同 /new",
            "  /status  — 查看当前运行状态",
            "  /help    — 显示此帮助",
        ]

        # Agent 层命令来自 session.commands（CommandLoader 加载的 .md 文件）
        agent_lines = []
        session = self._session_manager.get(session_key)
        if session:
            for cmd_info in session.commands.list_commands():
                desc = cmd_info.get("description", "")
                agent_lines.append(f"  {cmd_info['name']}  — {desc}")

        lines = gateway_lines
        if agent_lines:
            lines += ["", "**Agent 上下文命令（进入 AI 上下文）：**"] + agent_lines

        return GatewayCommandResult(handled=True, reply="\n".join(lines))
