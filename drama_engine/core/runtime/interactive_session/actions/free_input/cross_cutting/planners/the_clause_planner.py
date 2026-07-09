"""TheClause 专用规划器。

系统内置实现，注册名: "the_clause_planner"。
内部走 LLM executor，使用 planner.py 中定义的 prompt 模板。

DSL 用法:
    planner:
      name: the_clause_planner
      config:
        max_outline_steps: 4
        include_roles: true
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.runtime.interactive_session.actions.free_input.cross_cutting.planners.llm_planner import (
    LLMPlanner,
)
from drama_engine.core.runtime.interactive_session.actions.free_input.prompts.planner import (
    PLANNER_SYSTEM_PROMPT,
    PLANNER_USER_TEMPLATE,
)

logger = logging.getLogger(__name__)


class TheClausePlanner(LLMPlanner):
    """the_clause 专用规划器。

    使用 PLANNER_SYSTEM_PROMPT + PLANNER_USER_TEMPLATE 组装 prompt，
    注入剧本设定、角色、近期事件、玩家行动。
    """

    def build_prompt(
        self,
        player_action: str,
        context: dict[str, Any],
        ctx: Any,
    ) -> str:
        """组装 the_clause 规划 prompt。

        从 context 和 ctx 中提取:
          - story_setting: 剧本设定
          - roles: 角色信息
          - recent_events: 近期剧情
          - player_action: 玩家行动
        """
        # 提取剧本设定
        story_setting = self._extract_story_setting(context, ctx)

        # 提取角色信息
        roles_block = self._extract_roles_block(context, ctx)

        # 提取近期事件
        recent_events = self._extract_recent_events(context, ctx)

        # 拼装完整 prompt（system + user）
        user_prompt = PLANNER_USER_TEMPLATE.format(
            story_setting=story_setting,
            roles_block=roles_block,
            recent_events=recent_events,
            player_action=player_action or "（无明确行动，继续推进剧情）",
        )

        # system + user 拼接
        return f"{PLANNER_SYSTEM_PROMPT}\n\n---\n\n{user_prompt}"

    def _extract_story_setting(self, context: dict[str, Any], ctx: Any) -> str:
        """从上下文提取剧本设定。"""
        # 优先从 context 直接取
        if context.get("story_setting"):
            return str(context["story_setting"])

        # 从 script 取 setting/outline
        script = getattr(ctx, "script", None)
        if script is None:
            return "（未提供剧本设定）"

        parts = []
        meta = getattr(script, "meta", {}) or {}
        if meta.get("setting"):
            parts.append(str(meta["setting"]))
        if meta.get("outline"):
            parts.append(f"大纲: {meta['outline']}")
        if meta.get("description"):
            parts.append(str(meta["description"]))

        return "\n".join(parts) if parts else "（未提供剧本设定）"

    def _extract_roles_block(self, context: dict[str, Any], ctx: Any) -> str:
        """从上下文提取角色信息块。"""
        # 优先从 context 取
        if context.get("roles_block"):
            return str(context["roles_block"])

        # 从 context.characters 构造
        characters = context.get("characters") or []
        if not characters:
            # 尝试从 script.meta.roles 取
            script = getattr(ctx, "script", None)
            meta = getattr(script, "meta", {}) or {}
            roles = meta.get("roles") or []
            if isinstance(roles, list):
                characters = roles

        if not characters:
            return "（未提供角色信息）"

        lines = []
        for char in characters:
            if isinstance(char, dict):
                name = char.get("name") or char.get("id") or "?"
                persona = char.get("persona") or char.get("description") or ""
                # 截断过长的人设
                if len(persona) > 300:
                    persona = persona[:300] + "..."
                lines.append(f"- {name}: {persona}")
            elif isinstance(char, str):
                lines.append(f"- {char}")
        return "\n".join(lines) if lines else "（未提供角色信息）"

    def _extract_recent_events(self, context: dict[str, Any], ctx: Any) -> str:
        """从上下文提取近期事件摘要。"""
        # 优先从 context 取
        if context.get("recent_events"):
            return str(context["recent_events"])

        # 从 message_history 取最近几条
        history = context.get("message_history") or []
        if not history:
            history = getattr(ctx, "message_history", []) or []

        if not history:
            return "（暂无剧情历史）"

        # 取最近 10 条消息
        recent = history[-10:]
        lines = []
        for msg in recent:
            if isinstance(msg, dict):
                speaker = msg.get("actor") or msg.get("speaker") or "旁白"
                text = msg.get("text") or msg.get("content") or ""
                if text:
                    lines.append(f"[{speaker}] {text[:200]}")
        return "\n".join(lines) if lines else "（暂无剧情历史）"


__all__ = ["TheClausePlanner"]
