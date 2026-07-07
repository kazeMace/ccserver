"""ProjectionProfile：game_pack 贡献的对外投影档案（interaction.v1 开放键富化）。

InteractionProjector 是游戏无关的，只填封闭安全键（role / primitive）。开放键
（widget / card.variant / props / panels）的"游戏语义皮肤"由各 game_pack 提供一个
ProjectionProfile 数据对象，projector 消费它来富化——projector 本身不认识任何具体游戏
（docs/interaction_protocol_design.md §9.3 / §四.3）。

ProjectionProfile 是**纯数据 + 查找方法**，不是可执行 mini-DSL：
- widget_by_scene：scene_id → widget 皮肤名（如 wolf_kill → "vote:night_kill"）。
- role_badges：内部 role 属性值 → 展示名（如 werewolf → 狼人），供 StateView 用。
- scope_styles：scope 名 → [底色, 边色, 标签]，供前端频道条渲染。
- props_by_scene：scene_id → 语义级 props（A 收敛，如 {show_vote_count: true}）。
- panels：从游戏状态提取哪些侧边栏面板（affinity/hand/board/stats/circles）的声明。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ProjectionProfile:
    """一个 game_pack 的对外投影档案（可选；缺省时 projector 只填封闭键）。"""

    widget_by_scene: dict[str, str] = field(default_factory=dict)
    props_by_scene: dict[str, dict[str, Any]] = field(default_factory=dict)
    role_badges: dict[str, str] = field(default_factory=dict)
    scope_styles: dict[str, list[str]] = field(default_factory=dict)
    # panels：面板名 → 声明 {source: 状态提取方式, ...}。projector/view 据此从状态取数。
    panels: dict[str, dict[str, Any]] = field(default_factory=dict)

    def widget_for(self, scene_id: str | None) -> str | None:
        """返回该 scene 的 widget 皮肤名；未声明返回 None（走 primitive 保底）。"""
        if not scene_id:
            return None
        return self.widget_by_scene.get(scene_id)

    def props_for(self, scene_id: str | None) -> dict[str, Any] | None:
        """返回该 scene 的语义级 props；未声明返回 None。"""
        if not scene_id:
            return None
        return self.props_by_scene.get(scene_id)

    def badge_for(self, role_value: str | None) -> str | None:
        """返回角色展示名；未声明返回 None（前端回退到 role 本身）。"""
        if not role_value:
            return None
        return self.role_badges.get(role_value)

    def merge(self, other: "ProjectionProfile") -> "ProjectionProfile":
        """合并另一个 profile（多 game_pack 时叠加；other 优先）。"""
        return ProjectionProfile(
            widget_by_scene={**self.widget_by_scene, **other.widget_by_scene},
            props_by_scene={**self.props_by_scene, **other.props_by_scene},
            role_badges={**self.role_badges, **other.role_badges},
            scope_styles={**self.scope_styles, **other.scope_styles},
            panels={**self.panels, **other.panels},
        )

    def to_dict(self) -> dict[str, Any]:
        """可序列化导出（供前端 /config 或调试）。"""
        return {
            "widget_by_scene": dict(self.widget_by_scene),
            "props_by_scene": dict(self.props_by_scene),
            "role_badges": dict(self.role_badges),
            "scope_styles": dict(self.scope_styles),
            "panels": dict(self.panels),
        }


EMPTY_PROFILE = ProjectionProfile()


__all__ = ["ProjectionProfile", "EMPTY_PROFILE"]
