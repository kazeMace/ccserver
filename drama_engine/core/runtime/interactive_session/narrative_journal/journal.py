"""叙事日志核心接口。

NarrativeJournal 是面向 pipeline 的高层接口，
封装 NarrativeStore 提供业务语义方法。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from drama_engine.core.runtime.interactive_session.narrative_journal.models import (
    ContentBlock,
    NarrativeNode,
    PlayerAction,
)
from drama_engine.core.runtime.interactive_session.narrative_journal.store import (
    JsonFileNarrativeStore,
    NarrativeStore,
)

logger = logging.getLogger(__name__)


class NarrativeJournal:
    """叙事日志。

    面向 pipeline 的高层接口，负责：
      - 记录预制节点跳转
      - 记录生成节点
      - 维护树结构（parent/children 关系）
      - 查询叙事树/路径

    使用方式：
      journal = NarrativeJournal(store)
      await journal.record_transition(session_id, user_id, ...)
      tree = await journal.get_tree(session_id, user_id)
    """

    def __init__(self, store: NarrativeStore | None = None, base_dir: str = "") -> None:
        """初始化叙事日志。

        参数:
            store: 存储实现（传入自定义实现）
            base_dir: JSON 文件存储目录（store 为 None 时使用默认 JsonFileNarrativeStore）
        """
        if store is not None:
            self._store = store
        else:
            dir_path = base_dir or "/tmp/narrative_journal"
            self._store = JsonFileNarrativeStore(dir_path)

    async def record_transition(
        self,
        session_id: str,
        user_id: str,
        from_scene_id: str,
        to_scene_id: str,
        player_action: PlayerAction,
        title: str = "",
        content: list[ContentBlock] | None = None,
        choices_presented: list[dict[str, Any]] | None = None,
        parent_node_id: str | None = None,
    ) -> NarrativeNode:
        """记录一次预制节点间的跳转。

        参数:
            session_id: session 标识
            user_id: 用户标识
            from_scene_id: 来源场景 id
            to_scene_id: 目标场景 id
            player_action: 玩家做了什么
            title: 节点标题（可选）
            content: 内容块列表（可选）
            choices_presented: 展示过的选项（可选）
            parent_node_id: 父节点 id（可选，用于维护树结构）

        返回:
            创建的 NarrativeNode
        """
        # 计算 depth
        depth = 0
        if parent_node_id:
            parent = await self._store.get_node(parent_node_id)
            if parent:
                depth = parent.depth + 1

        node = NarrativeNode(
            node_id=self._gen_id(),
            session_id=session_id,
            user_id=user_id,
            source="preset",
            preset_scene_id=to_scene_id,
            title=title or to_scene_id,
            content=content or [],
            choices_presented=choices_presented or [],
            player_action=player_action,
            parent_id=parent_node_id,
            depth=depth,
            branch_type="main",
            created_at=datetime.now(),
        )

        await self._store.save_node(node)

        # 更新父节点的 children_ids
        if parent_node_id:
            parent = await self._store.get_node(parent_node_id)
            if parent and node.node_id not in parent.children_ids:
                parent.children_ids.append(node.node_id)
                await self._store.update_node(
                    parent_node_id, {"children_ids": parent.children_ids}
                )

        logger.info(
            "[NarrativeJournal] 记录跳转: %s → %s (node=%s, depth=%d)",
            from_scene_id, to_scene_id, node.node_id, depth,
        )
        return node

    async def record_generated(
        self,
        session_id: str,
        user_id: str,
        parent_node_id: str | None,
        player_action: PlayerAction,
        title: str = "",
        synopsis: str = "",
        content: list[ContentBlock] | None = None,
        assets: list[str] | None = None,
        choices_presented: list[dict[str, Any]] | None = None,
        generation_config: dict[str, Any] | None = None,
        branch_type: str = "generated",
    ) -> NarrativeNode:
        """记录一个 AI 生成的节点。

        参数:
            session_id: session 标识
            user_id: 用户标识
            parent_node_id: 父节点 id
            player_action: 触发生成的玩家行动
            title: 标题（Planner 产出或 Generator 产出）
            synopsis: 简介
            content: 内容块列表
            assets: 关联资产路径
            choices_presented: 生成的选项
            generation_config: 生成配置快照
            branch_type: 分支类型（"generated" / "side_branch"）

        返回:
            创建的 NarrativeNode
        """
        # 计算 depth
        depth = 0
        if parent_node_id:
            parent = await self._store.get_node(parent_node_id)
            if parent:
                depth = parent.depth + 1

        node = NarrativeNode(
            node_id=self._gen_id(),
            session_id=session_id,
            user_id=user_id,
            source="generated",
            generation_config=generation_config,
            title=title,
            synopsis=synopsis,
            content=content or [],
            assets=assets or [],
            choices_presented=choices_presented or [],
            player_action=player_action,
            parent_id=parent_node_id,
            depth=depth,
            branch_type=branch_type,
            created_at=datetime.now(),
        )

        await self._store.save_node(node)

        # 更新父节点的 children_ids
        if parent_node_id:
            parent = await self._store.get_node(parent_node_id)
            if parent and node.node_id not in parent.children_ids:
                parent.children_ids.append(node.node_id)
                await self._store.update_node(
                    parent_node_id, {"children_ids": parent.children_ids}
                )

        logger.info(
            "[NarrativeJournal] 记录生成节点: title=%s (node=%s, depth=%d, branch=%s)",
            title, node.node_id, depth, branch_type,
        )
        return node

    async def get_tree(self, session_id: str, user_id: str) -> list[NarrativeNode]:
        """获取完整叙事树（所有节点）。

        返回按 depth + 创建时间排序的节点列表。
        """
        return await self._store.get_all_nodes(session_id, user_id)

    async def get_path(self, session_id: str, user_id: str) -> list[NarrativeNode]:
        """获取当前主路径（从根到最深叶子节点的线性链）。

        策略：找到最大 depth 的节点，沿 parent_id 回溯到根。
        """
        all_nodes = await self._store.get_all_nodes(session_id, user_id)
        if not all_nodes:
            return []

        # 构建 id → node 映射
        node_map = {n.node_id: n for n in all_nodes}

        # 找最深节点（如果多个同深度，取最新的）
        deepest = max(all_nodes, key=lambda n: (n.depth, n.created_at))

        # 沿 parent_id 回溯
        path: list[NarrativeNode] = []
        current: NarrativeNode | None = deepest
        while current is not None:
            path.append(current)
            if current.parent_id and current.parent_id in node_map:
                current = node_map[current.parent_id]
            else:
                current = None

        path.reverse()
        return path

    async def get_node(self, node_id: str) -> NarrativeNode | None:
        """获取单个节点详情。"""
        return await self._store.get_node(node_id)

    async def update_player_duration(self, node_id: str, duration_ms: int) -> None:
        """更新玩家在节点的停留时长。"""
        await self._store.update_node(node_id, {"duration_ms": duration_ms})

    def _gen_id(self) -> str:
        """生成唯一节点 id。"""
        return f"nn_{uuid.uuid4().hex[:12]}"


__all__ = ["NarrativeJournal"]
