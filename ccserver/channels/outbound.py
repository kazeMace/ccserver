"""
channels/outbound — 出站消息投递。

职责：手动出站（API 调用/后台推送）和错误回复。
从 ChannelGateway 拆出，单一职责：只管往 channel 发消息。
"""

from typing import Optional

from loguru import logger

from .registry import ChannelRegistry


class OutboundDispatcher:
    """
    手动出站消息投递器。

    通过 session.default_output_targets 找到目标 channel，
    调用对应 adapter 的 send_text() 发送消息。

    Attributes:
        _registry:        ChannelRegistry 实例
        _session_manager: SessionManager 实例
    """

    def __init__(self, registry: ChannelRegistry, session_manager):
        self._registry = registry
        self._session_manager = session_manager

    async def dispatch(
        self,
        session_id: str,
        text: str,
        media_urls: Optional[list[str]] = None,
    ) -> dict:
        """
        手动发送出站消息（API 调用 / 后台推送）。

        优先使用 session.default_output_targets 中的第一个目标。

        Args:
            session_id:  Session ID
            text:        消息文本
            media_urls:  媒体 URL 列表（暂未使用）

        Returns:
            {"success": bool, "error": str}
        """
        session = self._session_manager.get(session_id)
        if session is None:
            return {"success": False, "error": "Session not found"}

        targets = session.default_output_targets or session.output_targets
        if not targets:
            logger.warning(
                "OutboundDispatcher: no output targets | session={}",
                session_id[:8],
            )
            return {"success": False, "error": "No output targets found"}

        target = targets[0]
        adapter = self._registry.get_adapter(target.channel_id)
        if adapter is None:
            return {"success": False, "error": f"Adapter not found: {target.channel_id}"}

        return await adapter.send_text(
            target.account_id,
            target.to,
            text,
            reply_to_id=target.reply_to_id,
        )

    async def send_error_reply(self, session_id: str, error_msg: str) -> None:
        """
        Agent 运行失败时，通过 default_output_targets 向用户发送错误提示。

        Args:
            session_id:  Session ID
            error_msg:   错误信息
        """
        session = self._session_manager.get(session_id)
        if session is None:
            return

        targets = session.default_output_targets or session.output_targets
        text = f"抱歉，处理消息时出错了：{error_msg}"

        for target in targets:
            try:
                adapter = self._registry.get_adapter(target.channel_id)
                if adapter is not None:
                    await adapter.send_text(
                        target.account_id,
                        target.to,
                        text,
                        reply_to_id=target.reply_to_id,
                    )
            except Exception as e:
                logger.error(
                    "send_error_reply failed | session={} channel={} err={}",
                    session_id[:8], target.channel_id, e,
                )
