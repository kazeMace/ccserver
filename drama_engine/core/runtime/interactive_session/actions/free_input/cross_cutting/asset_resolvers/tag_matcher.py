"""标签匹配资产解析器。

基于标签交集计算匹配得分，选择最佳资产。
后端: builtin（无需 LLM）。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.base import AssetResolver
from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.models import AssetMatch

logger = logging.getLogger(__name__)


class TagMatcherAssetResolver(AssetResolver):
    """基于标签交集的资产匹配器。

    原理：
      1. 从生成内容中提取关键词/标签
      2. 计算每个资产的标签与内容标签的交集大小
      3. 按交集得分排序，返回最佳匹配
    """

    async def resolve(
        self,
        content: dict[str, Any],
        asset_pool: list[dict[str, Any]],
        ctx: Any,
    ) -> list[AssetMatch]:
        """从资产池中匹配资产。

        参数:
            content: 生成内容
                - narration (str): 叙述文本
                - characters_involved (list[str]): 涉及角色
                - title (str): 标题
                - asset_hints (dict): Planner 产出的提示
            asset_pool: 资产列表
                [{"id": "bg_01", "tags": ["night", "garden"], "path": "...", "role": "background"}, ...]
            ctx: InteractiveExecutionContext
        """
        if not asset_pool:
            return []

        # 从内容中提取搜索标签
        content_tags = self._extract_content_tags(content)
        if not content_tags:
            # 没有标签可匹配，返回第一个作为 fallback
            first = asset_pool[0]
            return [AssetMatch(
                asset_id=str(first.get("id", "")),
                path=str(first.get("path", "")),
                role=str(first.get("role", "background")),
                score=0.1,
                metadata={"reason": "no_tags_fallback"},
            )]

        # 计算每个资产的匹配得分
        scored: list[tuple[float, dict[str, Any]]] = []
        for asset in asset_pool:
            asset_tags = set(str(t).lower() for t in (asset.get("tags") or []))
            if not asset_tags:
                continue
            # 交集大小 / 资产标签数 = 匹配比例
            intersection = content_tags & asset_tags
            score = len(intersection) / len(asset_tags) if asset_tags else 0.0
            if score > 0:
                scored.append((score, asset))

        # 按得分排序
        scored.sort(key=lambda x: x[0], reverse=True)

        # 取 top N
        max_results = int(self._config.get("max_results", 3))
        results: list[AssetMatch] = []
        for score, asset in scored[:max_results]:
            results.append(AssetMatch(
                asset_id=str(asset.get("id", "")),
                path=str(asset.get("path", "")),
                role=str(asset.get("role", "background")),
                score=score,
                metadata={"matched_tags": list(content_tags & set(str(t).lower() for t in (asset.get("tags") or [])))},
            ))

        return results

    def _extract_content_tags(self, content: dict[str, Any]) -> set[str]:
        """从生成内容中提取标签关键词。"""
        tags: set[str] = set()

        # 从 asset_hints 提取（Planner 产出的）
        hints = content.get("asset_hints") or {}
        if isinstance(hints, dict):
            for v in hints.values():
                if isinstance(v, str):
                    tags.add(v.lower())
                elif isinstance(v, list):
                    tags.update(str(t).lower() for t in v)

        # 从 characters_involved 提取
        characters = content.get("characters_involved") or []
        tags.update(str(c).lower() for c in characters)

        # 从 title 中提取词
        title = str(content.get("title", ""))
        if title:
            # 简单分词：按空格和标点分割
            words = title.lower().replace(",", " ").replace(".", " ").split()
            tags.update(w for w in words if len(w) > 2)

        return tags


__all__ = ["TagMatcherAssetResolver"]
