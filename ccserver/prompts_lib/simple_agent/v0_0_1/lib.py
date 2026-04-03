# prompts_lib/simple_agent/v0_0_1/lib.py

from __future__ import annotations

from ccserver.prompts_lib.base import PromptLib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ccserver.session import Session



class SimpleAgentV001(PromptLib):
    """
    simple_agent:v0.0.1 的提示词拼接实现。

    system prompt 结构：
      将 injected_system 文本加上当前日期后包装为 content 块。

    user message 注入模式：
      第一条消息注入技能目录和 hook 上下文（<system-reminder> 块），
      后续消息直接传递原始文本。
    """

    # lib_id 中的版本号，如 "simple_agent:v0.0.1" → "0.0.1"
    _VERSION = "0.0.1"

    def build_system(self, session: Session, model: str, language: str, cch: str = "", injected_system: list | None = None, append_system: bool = True, is_spawn: bool = False) -> list:
        if not injected_system:
            return []
        from datetime import datetime, timezone
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        text = f"{injected_system}\n\n# currentDate\nToday's date is {date_str}."
        return [{"type": "text", "text": text}]

    def build_skill_catalog(self, skills: list) -> str:
        """将 skill 列表格式化为注入消息的目录文本。"""
        if not skills:
            return ""
        entries = "\n".join(
            f"- {s['name']}: {s['description']}"
            for s in skills
        )
        return entries

    def build_command_message(self, cmd_info: dict, session: Session, history: list) -> list:
        parts = []
        name = cmd_info.get("name", "")
        parts.append({"type": "text", "text": name})
        return parts


    def build_user_message(self, text: str, session: Session, context: dict) -> list:

        parts = []

        if not context.get("is_first"):
            parts.append({"type": "text", "text": text})
            return parts
        # 1. skill inject（技能目录）
        skills_override = context.get("skills_override")  # None | list[str]

        if skills_override is None:
            # 根 agent：使用 session 全局 skills + commands
            local_skills = session.skills.list_skills() if session.skills else []
            local_commands = session.commands.list_commands() if session.commands else []
            all_entries = local_skills + local_commands
        elif len(skills_override) == 0:
            # subagent 未指定 skills：不注入任何 skill catalog
            all_entries = []
        else:
            # subagent 指定了 skills：只注入列出的 skill 名称
            allowed = set(skills_override)
            all_skills = session.skills.list_skills() if session.skills else []
            all_entries = [s for s in all_skills if s["name"] in allowed]

        skill_catalog = self.build_skill_catalog(all_entries)
        if skill_catalog:
            skill_text = f"The following skills are available for use with the Skill tool:\n\n{skill_catalog}"
            parts.append({"type": "text", "text": skill_text})

        # 2. UserPromptSubmit hook 附加上下文（如果有）
        hook_context = context.get("hook_context", "")
        if hook_context:
            parts.append({"type": "text", "text": hook_context})

        parts.append({"type": "text", "text": text})
        return parts
