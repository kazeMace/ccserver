"""跨模式组件数据结构定义。

定义 Free-Input Pipeline 中各组件间传递的数据类型：
  - GuardResult: 守卫校验结果
  - PlanResult: 规划器输出
  - AssetMatch: 资产匹配结果
  - GenerationInput: 生成流程统一输入
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class GuardResult:
    """守卫校验结果。

    属性:
        passed: 是否通过校验
        reason: 拒绝原因（passed=False 时填写）
        corrected_payload: 修正后的 payload（可选，支持自动修正而非拒绝）
        suggestions: 建议的替代输入（拒绝时可提供给玩家）
        metadata: 额外信息（日志/调试用）
    """

    passed: bool
    reason: str = ""
    corrected_payload: dict[str, Any] | None = None
    suggestions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PlanResult:
    """规划器输出。

    属性:
        title: 段落/场景标题
        synopsis: 一句话概要
        characters_involved: 涉及的角色名列表
        outline: 分步纲要（可选）
        asset_hints: 给 AssetResolver 的提示信息
        metadata: 额外信息
    """

    title: str = ""
    synopsis: str = ""
    characters_involved: list[str] = field(default_factory=list)
    outline: list[str] = field(default_factory=list)
    asset_hints: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AssetMatch:
    """资产匹配结果。

    属性:
        asset_id: 资产唯一标识
        path: 资产文件路径或 URL
        role: 资产角色类型
            - "background": 背景图
            - "character_state": 角色立绘状态
            - "bgm": 背景音乐
            - "sfx": 音效
        score: 匹配得分（0.0 ~ 1.0，越高越匹配）
        metadata: 额外信息（匹配标签、选择原因等）
    """

    asset_id: str
    path: str
    role: str = "background"
    score: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GenerationInput:
    """生成流程的统一输入。

    属性:
        player_action: 玩家的行动描述
            - 选了无 to 的 choice 时: choice.text
            - 自由输入时: 玩家输入的原始文本
            - 点继续时: 空字符串
        trigger_source: 触发来源标识
            - "choice_no_target": 选了无 to 的 choice
            - "free_input": 自由输入（未映射或映射失败）
            - "continue": 点了继续按钮
            - "mapper_fallback": mapper 映射到无 to 的 choice
        scene_context: 当前场景上下文（历史消息、角色列表、状态快照）
        plan: Planner 的产出（未配置 Planner 时为 None）
    """

    player_action: str
    trigger_source: str
    scene_context: dict[str, Any] = field(default_factory=dict)
    plan: PlanResult | None = None


__all__ = [
    "GuardResult",
    "PlanResult",
    "AssetMatch",
    "GenerationInput",
]
