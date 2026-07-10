"""Prompt 组装函数。

将各组件提供的 prompt 片段组装成完整的 system_prompt 和 user_prompt。
纯函数，不依赖运行时状态。
"""

from __future__ import annotations

from typing import Any


def _format_roles_block(roles: list[dict[str, Any]] | None) -> str | None:
    """把角色人设列表格式化为 prompt 片段。

    参数:
        roles: 角色 dict 列表（含 display_name/description 等）

    返回:
        格式化文本，无角色则返回 None
    """
    if not roles:
        return None
    lines = []
    for role in roles:
        if not isinstance(role, dict):
            continue
        name = role.get("display_name") or role.get("name") or ""
        desc = role.get("description") or role.get("persona") or ""
        if not name:
            continue
        # 人设描述可能很长，截断避免 prompt 过大
        desc_text = str(desc).strip().replace("\n", " ")
        if len(desc_text) > 300:
            desc_text = desc_text[:300] + "…"
        lines.append(f"- {name}：{desc_text}" if desc_text else f"- {name}")
    return "\n".join(lines) if lines else None


def assemble_system_prompt(
    narration_format: str,
    narration_schema: str,
    writing_style: str,
    choices_instruction: str | None,
    directive: str | None = None,
    ending_ids: list[str] | None = None,
    story_setting: str | None = None,
    roles: list[dict[str, Any]] | None = None,
) -> str:
    """组装 system prompt。

    参数:
        narration_format: 续写风格的输出格式说明
        narration_schema: 期望的 JSON schema 示例
        writing_style: 写作风格指令
        choices_instruction: 互动方式的 choices 格式要求（None = 不需要）
        directive: 用户自定义创作指令（可选）
        ending_ids: 可选结局 id 列表（用于告知 LLM 可用的 ending_id 值）
        story_setting: 剧本设定/大纲（软约束，锚定世界观基调）
        roles: 角色人设列表（软约束，确保对话符合人设）

    返回:
        完整 system prompt 字符串
    """
    parts = [
        "你是互动叙事引擎的剧情生成器。根据玩家输入和剧情上下文，生成下一个场景节点。",
    ]

    # ── 软约束：剧本设定（世界观锚点）──
    if story_setting:
        parts.append("")
        parts.append("## 剧本设定")
        parts.append(story_setting)

    # ── 软约束：角色人设（角色锚点）──
    roles_block = _format_roles_block(roles)
    if roles_block:
        parts.append("")
        parts.append("## 登场角色")
        parts.append(roles_block)
        parts.append("生成的对话必须符合以上角色的人设和语气，不要凭空捏造新角色。")

    parts.extend([
        "",
        "## 输出格式",
        "返回严格 JSON（不要 markdown 代码块），结构如下：",
        narration_schema,
        "",
        narration_format,
    ])

    if choices_instruction:
        parts.append("")
        parts.append("## 选项要求")
        parts.append(choices_instruction)

    parts.append("")
    parts.append("## 收束控制")
    parts.append("- should_end: 当剧情应收束时设为 true，并指定 ending_id")
    if ending_ids:
        parts.append(f"- ending_id 可选值: {ending_ids}")
    else:
        parts.append("- ending_id: 当前无预设结局，设为 null")

    parts.append("")
    parts.append("## 写作风格")
    parts.append(writing_style)
    parts.append("- 保持叙事连贯，不要重复已有内容")
    parts.append("- 每次生成应推动剧情发展")

    if directive:
        parts.append("")
        parts.append("## 额外指令")
        parts.append(directive)

    return "\n".join(parts)


def assemble_user_prompt(
    player_text: str,
    story_summary: str | None = None,
    recent_messages: list[str] | None = None,
    depth: int = 0,
    max_depth: int = 0,
    total_count: int = 0,
    max_count: int = 0,
    hint: str | None = None,
) -> str:
    """组装 user prompt。

    参数:
        player_text: 玩家输入文本
        story_summary: 剧情概要（可选）
        recent_messages: 最近消息列表（可选）
        depth: 当前生长深度
        max_depth: 最大深度（0 = 无限）
        total_count: 已生成总数
        max_count: 最大总数（0 = 无限）
        hint: 收束提示文本（由 Constraint 提供，可选）

    返回:
        完整 user prompt 字符串
    """
    parts = []

    if story_summary:
        parts.append("## 剧情概要")
        parts.append(story_summary)
        parts.append("")

    if recent_messages:
        parts.append("## 最近事件")
        for msg in recent_messages[-5:]:
            parts.append(f"- {msg}")
        parts.append("")

    # 状态信息
    parts.append("## 当前状态")
    depth_str = f"{depth}/{max_depth}" if max_depth > 0 else f"{depth}/∞"
    count_str = f"{total_count}/{max_count}" if max_count > 0 else f"{total_count}/∞"
    parts.append(f"深度: {depth_str}，已生成: {count_str}")

    if hint:
        parts.append("")
        parts.append(hint)

    parts.append("")
    parts.append("## 玩家输入")
    parts.append(player_text or "(无输入)")

    return "\n".join(parts)


def extract_story_summary(messages: list[dict[str, Any]], max_chars: int = 500) -> str:
    """从消息历史中提取剧情概要。

    参数:
        messages: 消息历史列表
        max_chars: 最大字符数

    返回:
        概要文本
    """
    if not messages:
        return ""

    texts = []
    total = 0
    for msg in reversed(messages):
        text = ""
        if isinstance(msg, dict):
            text = str(msg.get("text") or msg.get("content", {}).get("text") or "")
        if not text:
            continue
        if total + len(text) > max_chars:
            break
        texts.insert(0, text)
        total += len(text)

    return "\n".join(texts)


def extract_recent_messages(messages: list[dict[str, Any]], count: int = 5) -> list[str]:
    """从消息历史中提取最近 N 条摘要。

    参数:
        messages: 消息历史列表
        count: 提取条数

    返回:
        摘要列表
    """
    result = []
    for msg in messages[-count:]:
        if not isinstance(msg, dict):
            continue
        speaker = msg.get("actor") or msg.get("sender") or msg.get("speaker") or "system"
        text = str(msg.get("text") or msg.get("content", {}).get("text") or "")
        if text:
            result.append(f"[{speaker}] {text[:80]}")
    return result
