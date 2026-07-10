"""叙事日志数据模型。

定义叙事树的节点结构和关联数据类型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class ContentBlock:
    """内容块：叙事节点中的一段内容。

    属性:
        type: 内容类型
            - "narration": 旁白/叙述
            - "dialogue": 角色对话
            - "media": 媒体（图片/视频/音频）
        speaker: 说话者（type=dialogue 时填写，narration 时为空）
        text: 文本内容
        media_url: 媒体 URL（type=media 时填写）
        media_kind: 媒体类型（image / video / audio）
        metadata: 额外信息
    """

    type: str
    text: str = ""
    speaker: str = ""
    media_url: str = ""
    media_kind: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PlayerAction:
    """玩家行动记录。

    属性:
        type: 行动类型
            - "choice": 选了一个预制/生成的选项
            - "free_input": 自由输入文字
            - "continue": 点了继续按钮
        value: 行动内容（choice.text / 输入文本 / 空）
        choice_id: 选择的 choice id（type=choice 时填写）
        metadata: 额外信息（如 mapper 置信度等）
    """

    type: str
    value: str = ""
    choice_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NarrativeNode:
    """叙事树节点。

    每一次"场景展示→玩家反应→跳转/生成"构成一个节点。
    无论是预制场景跳转还是 AI 生成的新场景，都统一记录。

    属性:
        node_id: 唯一标识（uuid）
        session_id: 所属 session
        user_id: 所属用户

        source: 节点来源
            - "preset": 预制场景（编剧写的）
            - "generated": AI 生成的场景
        preset_scene_id: preset 时对应 DSL 中的 scene_id
        generation_config: generated 时的生成配置快照

        title: 节点标题
        synopsis: 简介/摘要
        content: 内容块列表（旁白/对话/媒体）
        assets: 关联资产路径列表

        choices_presented: 展示给玩家的选项列表
        player_action: 玩家实际做了什么

        parent_id: 父节点 id（根节点为 None）
        children_ids: 子节点 id 列表
        depth: 在树中的深度（根=0）
        branch_type: 分支类型
            - "main": 主线
            - "side_branch": 旁支（branch_return 产生的）
            - "generated": 生长出的新分支

        created_at: 创建时间
        duration_ms: 玩家在此节点的停留时长（毫秒）
    """

    node_id: str
    session_id: str
    user_id: str

    # 来源
    source: str = "preset"
    preset_scene_id: str | None = None
    generation_config: dict[str, Any] | None = None

    # 内容
    title: str = ""
    synopsis: str = ""
    content: list[ContentBlock] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)

    # 玩家交互
    choices_presented: list[dict[str, Any]] = field(default_factory=list)
    player_action: PlayerAction | None = None

    # 树结构
    parent_id: str | None = None
    children_ids: list[str] = field(default_factory=list)
    depth: int = 0
    branch_type: str = "main"

    # 元数据
    created_at: datetime = field(default_factory=datetime.now)
    duration_ms: int | None = None


__all__ = [
    "ContentBlock",
    "PlayerAction",
    "NarrativeNode",
]
