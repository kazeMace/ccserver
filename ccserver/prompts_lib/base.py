# src/prompts_lib/base.py

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ccserver.session import Session
    from ccserver.settings import ProjectSettings
    from ccserver.model import ModelAdapter
    from ccserver.builtins.tools import BuiltinTools
    from ccserver.emitters.base import BaseEmitter


class PromptLib:
    """
    Prompt Lib 基类。
    每个具体的 lib 继承这个类，覆盖需要定制的方法。

    三个方法对应三个拼接场景：
      build_system        — 启动时构建 system prompt 列表
      build_user_message  — 每条 user 消息追加前的包装
      build_compact_messages — 压缩完成后写回 history 的消息格式
    """

    def build_system(self, session: Session, model: str, language: str, cch: str = "", injected_system: list | None = None, is_spawn: bool = False) -> list:
        """
        返回传给 Anthropic API 的 system 列表，每项是 {"type": "text", "text": "..."}。
        injected_system 是调用方额外传入的 system 块，子类内部决定如何处理（追加/替换/忽略）。
        """
        raise NotImplementedError

    def build_user_message(self, text: str, session: Session, context: dict) -> list:
        """
        对 user 消息做包装后返回，结果直接作为 message["content"]。
        返回列表格式，每项是 {"type": "text", "text": "..."}。

        默认实现：不做任何处理，直接将文本包装成列表返回。
        子类可以覆盖此方法注入 system-reminder 等内容。
        """
        return [{"type": "text", "text": text}]

    def on_message(self, message: dict, session: Session, history: list, skills_override=None) -> dict:
        """
        消息追加到 history 之前的统一处理入口。
        返回处理后的 message（可以是原对象或新对象）。

        支持两种 user content 格式：
          str  — 普通文本消息，调用 build_user_message 包装
          dict — 结构化消息，目前支持 _type=command

        history 是当前已有的消息列表（不含本条），可用于判断 is_first 等。

        skills_override:
          None        — 使用 session 全局 skills（根 agent 默认行为）
          list[str]   — 只允许使用列出的 skill 名称（空列表表示无任何 skill）
        """
        if message["role"] != "user":
            return message
        content = message.get("content")

        if isinstance(content, str):
            is_first = not any(m["role"] == "user" for m in history)
            blocks = self.build_user_message(
                content, session,
                context={"is_first": is_first, "skills_override": skills_override},
            )
            return {"role": "user", "content": blocks}

        if isinstance(content, dict) and content.get("_type") == "command":
            blocks = self.build_command_message(content, session, history)
            return {"role": "user", "content": blocks}

        return message

    def build_skill_catalog(self, skills: list) -> str:
        """
        将 skill 元数据列表格式化为注入消息的 catalog 文本。
        skills 是 SkillLoader.list_skills() 的返回值，每项含 name/description/location。
        返回空字符串表示无 skill 或不需要注入。
        子类覆盖此方法以定制格式。
        """
        if not skills:
            return ""
        entries = "\n".join(
            f"  <skill>\n    <name>{s['name']}</name>\n    <description>{s['description']}</description>\n    <location>{s['location']}</location>\n  </skill>"
            for s in skills
        )
        return f"<available_skills>\n{entries}\n</available_skills>"

    def build_command_message(self, cmd_info: dict, session: Session, history: list) -> list:
        """
        将 command 信息包装为 content block 列表。
        cmd_info 字段：name, args, stdout, body

        默认实现：直接拼接成可读文本块，子类可覆盖实现更精确的格式。
        """
        parts = []
        name = cmd_info.get("name", "")
        args = cmd_info.get("args", "")
        stdout = cmd_info.get("stdout", "")
        body = cmd_info.get("body", "")

        cmd_block = f"<command-name>/{name}</command-name>\n<command-message>{name}</command-message>\n<command-args>{args}</command-args>"
        parts.append({"type": "text", "text": cmd_block})

        if stdout:
            parts.append({"type": "text", "text": f"<local-command-stdout>{stdout}</local-command-stdout>"})

        if body:
            suffix = f"\n\nARGUMENTS: {args}" if args else ""
            parts.append({"type": "text", "text": f"{body}{suffix}"})

        return parts

    def patch_tool_schemas(self, schemas: list[dict]) -> list[dict]:
        """
        对工具 schema 列表做后处理（替换 description、参数描述等）。
        在 factory/spawn_child 生成 _schemas 后调用。

        默认实现：原样返回，不做任何修改。
        子类可覆盖此方法替换工具描述，使其符合特定 prompt lib 的风格。

        schemas: [{"name": ..., "description": ..., "input_schema": {...}}, ...]
        返回修改后的列表（可以是原列表 in-place 修改后返回）。
        """
        return schemas

    def build_tools(
        self,
        session: "Session",
        adapter: "ModelAdapter",
        settings: "ProjectSettings",
        emitter: "BaseEmitter | None" = None,
    ) -> dict[str, "BuiltinTools"]:
        """
        返回该 PromptLib 所管理的内置工具字典。
        在 factory.py 调用 AgentFactory.create_root() 时被调用。

        子类可覆盖此方法：
          1. 完全替换工具集（如某些 lib 不需要特定工具）
          2. 自定义工具初始化参数（如 WebFetch 使用不同 model）
          3. 注入额外的自定义工具

        默认实现：返回 ccserver 内置工具的默认集合。
        """
        from ccserver.builtins.tools import (
            BTBash,
            BTRead,
            BTWrite,
            BTEdit,
            BTGlob,
            BTGrep,
            BTCompact,
            BTTaskCreate,
            BTTaskUpdate,
            BTTaskGet,
            BTTaskList,
            BTTaskStop,
            BTAskUser,
            BTWebFetch,
            BTWebSearch,
            BTAgent,
            BTSendMessage,
        )
        from ccserver.model import AnthropicAdapter

        # 基础工具（不需要 LLM client）
        tools: dict[str, BuiltinTools] = {
            "Bash": BTBash(session.project_root, settings, session=session, emitter=emitter),
            "Read": BTRead(session.project_root),
            "Write": BTWrite(session.project_root),
            "Edit": BTEdit(session.project_root),
            "Glob": BTGlob(session.project_root),
            "Grep": BTGrep(session.project_root),
            "Compact": BTCompact(),
            "TaskCreate": BTTaskCreate(session.tasks),
            "TaskUpdate": BTTaskUpdate(session.tasks),
            "TaskGet": BTTaskGet(session.tasks),
            "TaskList": BTTaskList(session.tasks),
            "TaskStop": BTTaskStop(session.shell_tasks, session=session),
            "AskUser": BTAskUser(),
        }

        # 需要 LLM client 的工具
        if adapter is not None:
            tools["WebFetch"] = BTWebFetch(adapter)
            if isinstance(adapter, AnthropicAdapter):
                tools["WebSearch"] = BTWebSearch(adapter)

        # Agent 工具动态注入
        tools["Agent"] = BTAgent(agent_catalog=session.agents.build_catalog())

        # Agent Team 通信工具（仅在开启 team 功能时注册）
        if session.settings.user_agent_team:
            tools["SendMessage"] = BTSendMessage()

        return tools

    def build_compact_messages(self, summary: str, transcript_ref: str) -> list:
        """
        LLM 压缩完成后，用什么格式写回 history。
        返回两条消息：一条 user（含摘要），一条 assistant（确认）。
        """
        return [
            {"role": "user",      "content": f"[Compressed. Transcript: {transcript_ref}]\n\n{summary}"},
            {"role": "assistant", "content": "Understood. Continuing."},
        ]
