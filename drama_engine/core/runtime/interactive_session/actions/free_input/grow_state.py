"""grow_flow 生长状态追踪。

GrowFlowState 管理一局 session 内 grow_flow 的生长状态：
  - 已生成的场景总数
  - 每个生成场景的深度（距原始场景的层数）
  - 父子关系映射

状态存储在 session_metadata["__grow_flow_state"]，跟随 session 生命周期。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# session_metadata 中的存储 key
_STATE_KEY = "__grow_flow_state"


class GrowFlowState:
    """grow_flow 生长状态追踪器。

    数据结构（存储在 session_metadata[_STATE_KEY]）：
    {
        "total_count": 3,
        "depth_map": {"grow_abc": 1, "grow_def": 2},
    }
    """

    def __init__(self, session_metadata: dict[str, Any]) -> None:
        """绑定到 session_metadata。

        参数:
            session_metadata: 运行时 session_metadata 字典（可变引用）
        """
        assert isinstance(session_metadata, dict), "session_metadata 必须是 dict"
        self._metadata = session_metadata
        # 确保状态结构存在
        if _STATE_KEY not in self._metadata:
            self._metadata[_STATE_KEY] = {
                "total_count": 0,
                "depth_map": {},
            }

    @property
    def _state(self) -> dict[str, Any]:
        """返回内部状态字典的引用。"""
        return self._metadata[_STATE_KEY]

    def register(self, scene_id: str, parent_scene_id: str) -> None:
        """注册一个新生成的场景。

        参数:
            scene_id: 新场景 id
            parent_scene_id: 父场景 id（触发生成的场景）
        """
        assert scene_id, "scene_id 不能为空"
        assert parent_scene_id, "parent_scene_id 不能为空"

        state = self._state
        # 计算深度：父场景深度 + 1
        parent_depth = state["depth_map"].get(parent_scene_id, 0)
        new_depth = parent_depth + 1

        state["total_count"] += 1
        state["depth_map"][scene_id] = new_depth

        logger.info(
            "[GrowFlowState] 注册场景: scene=%s parent=%s depth=%d total=%d",
            scene_id, parent_scene_id, new_depth, state["total_count"],
        )

    def depth_of(self, scene_id: str) -> int:
        """返回场景的生长深度。

        原始（手写）场景深度为 0，生成的场景从 1 开始。

        参数:
            scene_id: 场景 id

        返回:
            深度值
        """
        return self._state["depth_map"].get(scene_id, 0)

    def total_count(self) -> int:
        """返回已生成的场景总数。"""
        return self._state["total_count"]

    def should_force_ending(self, constraint_config: dict[str, Any], current_scene_id: str) -> bool:
        """判断是否应强制收束。

        参数:
            constraint_config: DSL constraint 配置块
            current_scene_id: 当前场景 id（即将作为父场景）

        返回:
            True = 应强制收束，不再生长

        禁用约定:
            所有数值参数设为 0 或不写（null）即表示禁用该维度约束。
            - max_count: 0 = 无总数限制
            - max_depth: 0 = 无深度上限
            - force_at_depth: 0 = 不提前强制（回退到 max_depth）
            两者都设时取较小值生效。
        """
        constraint_type = constraint_config.get("type", "free")
        if constraint_type == "free":
            return False

        # 检查 max_count（总生成场景数上限，0 = 无限制）
        max_count = int(constraint_config.get("max_count") or 0)
        if max_count > 0 and self.total_count() >= max_count:
            return True

        # 检查深度约束
        next_depth = self.depth_of(current_scene_id) + 1

        # max_depth: 绝对深度上限（0 = 无限制）
        max_depth = int(constraint_config.get("max_depth") or 0)
        if max_depth > 0 and next_depth >= max_depth:
            return True

        # force_at_depth: 提前强制收束深度（0 = 不启用，回退到 max_depth）
        force_at = int(constraint_config.get("force_at_depth") or 0)
        if force_at > 0 and next_depth >= force_at:
            return True

        return False

    def should_hint_ending(self, constraint_config: dict[str, Any], current_scene_id: str) -> bool:
        """判断是否应提示 LLM 收束。

        参数:
            constraint_config: DSL constraint 配置块
            current_scene_id: 当前场景 id

        返回:
            True = 应在 prompt 中注入收束提示

        禁用约定:
            hint_at_depth 设为 0 或不写即表示不注入提示。
        """
        hint_at = int(constraint_config.get("hint_at_depth") or 0)
        if hint_at <= 0:
            return False
        next_depth = self.depth_of(current_scene_id) + 1
        return next_depth >= hint_at

    def snapshot(self) -> dict[str, Any]:
        """返回当前状态的快照副本（用于注入生成上下文）。"""
        state = self._state
        return {
            "total_count": state["total_count"],
            "depth_map": dict(state["depth_map"]),
            "current_max_depth": max(state["depth_map"].values()) if state["depth_map"] else 0,
        }


__all__ = ["GrowFlowState"]
