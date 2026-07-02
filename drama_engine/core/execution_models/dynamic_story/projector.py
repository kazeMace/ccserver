"""Dynamic-story view projector."""

from __future__ import annotations

from typing import Any

from drama_engine.core.execution_models.dynamic_story.state import DynamicStoryState, WorldMemory
from drama_engine.core.ports.views import BaseViewProjector

class DynamicStoryViewProjector(BaseViewProjector):
    """Project dynamic story memory into ViewHost events."""

    def project(self, state: DynamicStoryState, world: WorldMemory) -> list[dict[str, Any]]:
        """Build ViewHost events for world state and memory."""
        memory_rows = [
            {"index": item.get("index"), "kind": item.get("kind"), "text": item.get("text") or item.get("consequence")}
            for item in state.memory[-12:]
        ]
        return [
            {
                "kind": "__view__",
                "view_id": "dynamic-story-world",
                "view_kind": "key-value",
                "title": "动态世界状态",
                "audience": "public",
                "priority": 20,
                "data": {
                    "rows": [
                        {"label": "世界", "value": state.world_name},
                        {"label": "前提", "value": state.premise},
                        {"label": "记忆数", "value": len(state.memory)},
                        {"label": "世界状态", "value": world.state},
                    ],
                },
            },
            {
                "kind": "__view__",
                "view_id": "dynamic-story-memory",
                "view_kind": "table",
                "title": "剧情记忆",
                "audience": "public",
                "priority": 10,
                "data": {
                    "columns": [
                        {"key": "index", "label": "序号"},
                        {"key": "kind", "label": "类型"},
                        {"key": "text", "label": "内容"},
                    ],
                    "rows": memory_rows,
                },
            },
        ]

__all__ = ["DynamicStoryViewProjector"]
