"""
send_message — Agent Team 内部通信工具。

允许 teammate 向其他队友或整个团队广播消息。
实际执行逻辑由 Agent._handle_send_message() 拦截处理，
此文件仅提供 LLM 可见的 schema 定义。
"""

from .base import BuiltinTools, ToolParam, ToolResult


class BTSendMessage(BuiltinTools):
    """
    在 Agent Team 中向指定队友发送消息。

    仅在 userAgentTeam=true 且 Agent 属于某个 Team 时注册到工具集。
    LLM 调用后，Agent._handle_send_message() 负责将消息写入目标 Mailbox。
    """

    name = "SendMessage"

    params = {
        "to": ToolParam(
            type="string",
            description='目标队友名称（如 "researcher"）或 "*" 表示向整个团队广播',
        ),
        "message": ToolParam(
            type="string",
            description="消息正文内容",
        ),
        "summary": ToolParam(
            type="string",
            description="5-10 词摘要，供 UI 预览用",
            required=False,
        ),
    }

    async def run(self, to: str, message: str, summary: str = "") -> ToolResult:
        """
        此方法仅作为 schema 占位，实际逻辑在 Agent._handle_send_message() 中处理。
        """
        return ToolResult.ok("")
