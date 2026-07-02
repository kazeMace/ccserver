"""Group-chat view projector."""

from __future__ import annotations

from typing import Any

from drama_engine.core.execution_models.group_chat.state import GroupChatState
from drama_engine.core.ports.views import BaseViewProjector

class GroupChatViewProjector(BaseViewProjector):
    """Project group-chat state into ViewHost events."""

    def project(self, state: GroupChatState) -> list[dict[str, Any]]:
        """Build public ViewHost events for transcript and summary."""
        rows = [
            {"speaker": item["speaker"], "round": item["round"], "text": item["text"]}
            for item in state.transcript
        ]
        return [
            {
                "kind": "__view__",
                "view_id": "group-chat-summary",
                "view_kind": "key-value",
                "title": "群聊摘要",
                "audience": "public",
                "priority": 20,
                "data": {
                    "rows": [
                        {"label": "房间", "value": state.room_name},
                        {"label": "主题", "value": state.topic},
                        {"label": "摘要", "value": state.summary},
                    ],
                },
            },
            {
                "kind": "__view__",
                "view_id": "group-chat-transcript",
                "view_kind": "table",
                "title": "群聊记录",
                "audience": "public",
                "priority": 10,
                "data": {
                    "columns": [
                        {"key": "round", "label": "轮次"},
                        {"key": "speaker", "label": "发言者"},
                        {"key": "text", "label": "内容"},
                    ],
                    "rows": rows,
                },
            },
        ]

__all__ = ["GroupChatViewProjector"]
