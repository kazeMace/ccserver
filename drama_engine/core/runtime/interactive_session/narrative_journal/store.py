"""叙事日志存储层协议和实现。

NarrativeStore: 存储协议（抽象接口）
JsonFileNarrativeStore: JSON 文件存储实现（开发阶段使用）
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from drama_engine.core.runtime.interactive_session.narrative_journal.models import (
    ContentBlock,
    NarrativeNode,
    PlayerAction,
)

logger = logging.getLogger(__name__)


class NarrativeStore(Protocol):
    """叙事日志存储协议。

    定义持久化层的抽象接口。
    生产环境可替换为数据库实现。
    """

    async def save_node(self, node: NarrativeNode) -> None:
        """保存一个叙事节点。"""
        ...

    async def update_node(self, node_id: str, updates: dict[str, Any]) -> None:
        """部分更新节点字段。"""
        ...

    async def get_node(self, node_id: str) -> NarrativeNode | None:
        """获取单个节点。"""
        ...

    async def get_all_nodes(self, session_id: str, user_id: str) -> list[NarrativeNode]:
        """获取指定 session 的所有节点。"""
        ...

    async def delete_session(self, session_id: str, user_id: str) -> None:
        """删除整个 session 的记录。"""
        ...


class JsonFileNarrativeStore:
    """JSON 文件存储实现。

    每个 session 一个 JSON 文件，存储在指定目录下。
    文件名格式: {user_id}_{session_id}.json

    适用于开发/测试阶段。生产环境应替换为数据库实现。
    """

    def __init__(self, base_dir: str) -> None:
        """初始化存储。

        参数:
            base_dir: 存储目录路径
        """
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _file_path(self, session_id: str, user_id: str) -> Path:
        """构造存储文件路径。"""
        safe_user = user_id.replace("/", "_").replace("\\", "_")
        safe_session = session_id.replace("/", "_").replace("\\", "_")
        return self._base_dir / f"{safe_user}_{safe_session}.json"

    def _load_data(self, session_id: str, user_id: str) -> dict[str, Any]:
        """从文件加载数据。"""
        path = self._file_path(session_id, user_id)
        if not path.exists():
            return {"nodes": {}, "meta": {"session_id": session_id, "user_id": user_id}}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_data(self, session_id: str, user_id: str, data: dict[str, Any]) -> None:
        """保存数据到文件。"""
        path = self._file_path(session_id, user_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    def _node_to_dict(self, node: NarrativeNode) -> dict[str, Any]:
        """将 NarrativeNode 序列化为 dict。"""
        d = asdict(node)
        # datetime 转为 ISO 字符串
        if isinstance(d.get("created_at"), datetime):
            d["created_at"] = d["created_at"].isoformat()
        return d

    def _dict_to_node(self, d: dict[str, Any]) -> NarrativeNode:
        """从 dict 反序列化为 NarrativeNode。"""
        # 还原 content 列表
        content_list = []
        for block in (d.get("content") or []):
            if isinstance(block, dict):
                content_list.append(ContentBlock(**block))
            else:
                content_list.append(block)

        # 还原 player_action
        pa = d.get("player_action")
        player_action = None
        if isinstance(pa, dict):
            player_action = PlayerAction(**pa)

        # 还原 created_at
        created_at = d.get("created_at")
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at)
            except ValueError:
                created_at = datetime.now()
        elif not isinstance(created_at, datetime):
            created_at = datetime.now()

        return NarrativeNode(
            node_id=d.get("node_id", ""),
            session_id=d.get("session_id", ""),
            user_id=d.get("user_id", ""),
            source=d.get("source", "preset"),
            preset_scene_id=d.get("preset_scene_id"),
            generation_config=d.get("generation_config"),
            title=d.get("title", ""),
            synopsis=d.get("synopsis", ""),
            content=content_list,
            assets=list(d.get("assets") or []),
            choices_presented=list(d.get("choices_presented") or []),
            player_action=player_action,
            parent_id=d.get("parent_id"),
            children_ids=list(d.get("children_ids") or []),
            depth=int(d.get("depth", 0)),
            branch_type=d.get("branch_type", "main"),
            created_at=created_at,
            duration_ms=d.get("duration_ms"),
        )

    async def save_node(self, node: NarrativeNode) -> None:
        """保存一个叙事节点。"""
        data = self._load_data(node.session_id, node.user_id)
        data["nodes"][node.node_id] = self._node_to_dict(node)
        self._save_data(node.session_id, node.user_id, data)
        logger.debug("[JsonFileNarrativeStore] 保存节点: %s", node.node_id)

    async def update_node(self, node_id: str, updates: dict[str, Any]) -> None:
        """部分更新节点字段。需要遍历所有文件查找（开发实现，不高效）。"""
        # 简单实现：遍历目录找到包含该 node_id 的文件
        for path in self._base_dir.glob("*.json"):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if node_id in data.get("nodes", {}):
                data["nodes"][node_id].update(updates)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2, default=str)
                return
        logger.warning("[JsonFileNarrativeStore] 未找到节点: %s", node_id)

    async def get_node(self, node_id: str) -> NarrativeNode | None:
        """获取单个节点。"""
        for path in self._base_dir.glob("*.json"):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if node_id in data.get("nodes", {}):
                return self._dict_to_node(data["nodes"][node_id])
        return None

    async def get_all_nodes(self, session_id: str, user_id: str) -> list[NarrativeNode]:
        """获取指定 session 的所有节点。"""
        data = self._load_data(session_id, user_id)
        nodes = []
        for node_dict in data.get("nodes", {}).values():
            nodes.append(self._dict_to_node(node_dict))
        # 按 depth 排序
        nodes.sort(key=lambda n: (n.depth, n.created_at))
        return nodes

    async def delete_session(self, session_id: str, user_id: str) -> None:
        """删除整个 session 的记录。"""
        path = self._file_path(session_id, user_id)
        if path.exists():
            os.remove(path)
            logger.info("[JsonFileNarrativeStore] 删除 session 文件: %s", path)


__all__ = [
    "NarrativeStore",
    "JsonFileNarrativeStore",
]
