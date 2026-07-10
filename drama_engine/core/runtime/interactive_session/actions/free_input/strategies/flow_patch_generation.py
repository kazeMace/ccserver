"""Flow 补丁生成策略（Flow Patch Generation）。

动态生成 flow 补丁，用于 grow_flow 模式。

适用场景：
  - 文字冒险：根据玩家输入动态添加新场景节点
  - 互动小说：生成临时剧情分支
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.strategies.base import (
    FreeInputStrategy,
)

logger = logging.getLogger(__name__)


class TemplateFlowPatchGenerationStrategy(FreeInputStrategy):
    """基于模板的 Flow 补丁生成（内置实现）。

    算法：
      1. 如果 spec.generator.patch 显式指定了 patch，直接返回
      2. 否则生成一个最简单的 add_scene patch
      3. 新 scene 的内容使用固定模板

    优点：无需外部依赖，适合 dry-run 测试
    缺点：无法根据玩家输入生成真正动态的场景
    """

    async def execute(
        self,
        mode: str,
        spec: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """生成 Flow 补丁。

        参数:
            mode: 固定为 "grow_flow"
            spec: DSL free_input 配置，可能包含 generator.patch
            context: 包含 text（玩家输入）、ctx（运行时上下文）、patch_journal

        返回:
            {
                "patch": {
                    "type": "add_scene",
                    "after": "当前场景id",
                    "scene": {...}
                }
            }
        """
        generator_spec = spec.get("generator", {}) if isinstance(spec.get("generator"), dict) else {}

        # 1. 如果显式指定了 patch，直接返回
        if isinstance(generator_spec.get("patch"), dict):
            logger.debug("[TemplateFlowPatch] 使用显式指定的 patch")
            return {"patch": dict(generator_spec["patch"])}

        # 2. 生成简单的 add_scene patch
        ctx = context.get("ctx")
        current_scene_id = getattr(ctx, "current_scene_id", None) if ctx else None
        patch_journal = context.get("patch_journal")
        if not patch_journal and ctx:
            patch_journal = getattr(ctx, "patch_journal", None)

        # 生成唯一 scene id
        existing_count = 0
        if patch_journal:
            existing_count = len(patch_journal.by_type("flow_patch"))
        scene_id = f"generated_{existing_count + 1}"

        # 从玩家输入提取文本
        text = str(context.get("text") or "新的剧情节点被创建。")

        patch = {
            "type": "add_scene",
            "after": current_scene_id,
            "scene": {
                "id": scene_id,
                "type": "scene",
                "scope": {
                    "id": "story",
                    "visibility": "public",
                },
                "participants": {"static": []},
                "schedule": {"mode": "none"},
                "participant_action": {
                    "kind": "none",
                    "response": {"mode": "none"},
                },
                "controller_action": {
                    "enabled": False,
                    "kind": "none",
                },
                "publication": {
                    "messages": [
                        {
                            "audience": {"scope": "story"},
                            "content": {"text": text},
                        }
                    ]
                },
            },
        }

        logger.info(
            "[TemplateFlowPatch] 生成 add_scene patch: scene_id=%s after=%s",
            scene_id,
            current_scene_id,
        )
        return {"patch": patch}


__all__ = ["TemplateFlowPatchGenerationStrategy"]
