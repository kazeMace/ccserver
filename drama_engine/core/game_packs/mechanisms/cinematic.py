"""播片式剧情机制（builtin.cinematic）。

适用于 Galgame / 视觉小说 / 互动影视类游戏：
- 玩家逐条阅读预制对话（旁白+角色台词），点击推进到下一句
- 只在关键分支节点做出选择影响剧情走向
- 支持视频/音频/背景图等多媒体素材

与 social（狼人杀）的区别：
- social：多人同时在线，每轮所有人发言/投票，LLM 实时生成
- cinematic：单人逐条阅读预制内容，点击推进，关键节点选择

注册内容：
- Effect `cinematic_emit_line`：发射单条对话事件（含音频URL等）
- Condition `cinematic.has_dialogue`：判断当前 scene 是否有对话历史
"""

from __future__ import annotations

from typing import Any


def register(api: Any) -> None:
    """注册 cinematic 机制到 PluginRegistry。"""
    api.register_effect("cinematic_emit_line", _handle_emit_line)


def _handle_emit_line(effect: dict, context: Any) -> None:
    """发射单条对话行事件。

    effect 字段：
      speaker    — 说话者（narrator / 角色名）
      text       — 台词内容
      target     — 对话对象（可选）
      sound_url  — 音频 URL（可选）
      location   — 场景地点（可选）
    """
    state = context.state
    writer = context.writer
    # 不需要写 state，这个 effect 纯粹是为了能在 hooks 中逐行 emit
    # 实际的播片逻辑由 controller kind=cinematic 处理


def build_cinematic_projection_profile() -> Any:
    """构建 cinematic 投影档案。

    panels:
    - cinematic_mode：前端识别播片模式
    - story_tree：剧情分支树数据（供前端渲染进度树）
    """
    from drama_engine.core.interaction.profile import ProjectionProfile
    return ProjectionProfile(
        panels={
            "cinematic_mode": {"mode": "visual_novel"},
            "story_tree": {"source": "story_tree"},
        },
    )


__all__ = ["register", "build_cinematic_projection_profile"]
