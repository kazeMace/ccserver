# ccserver/prompt_engine.py
#
# PromptEngine — ccserver 与 prompt_library 之间的适配层。
#
# 职责：
#   1. 持有 PromptLib 实例，代理所有 prompt 方法调用（build_system / on_message 等）
#   2. 承接强依赖 ccserver 的工具构建逻辑（build_tools / patch_tool_schemas）
#
# agent.py / factory.py 只和 PromptEngine 打交道，不直接 import prompts_lib。

from __future__ import annotations

from typing import TYPE_CHECKING

from ccserver.prompts_lib.adapter import get_lib
from ccserver.prompts_lib.base import PromptLib

if TYPE_CHECKING:
    from ccserver.session import Session
    from ccserver.settings import ProjectSettings
    from ccserver.model import ModelAdapter
    from ccserver.emitters.base import BaseEmitter


class PromptEngine:
    """
    ccserver 内部使用的 prompt 引擎。

    将 PromptLib 的 prompt 方法代理出去，
    同时在此处实现依赖 ccserver 内部模块的工具构建逻辑。

    Args:
        lib_id: prompt lib 的标识符，例如 "cc_reverse:v2.1.81"
    """

    def __init__(self, lib_id: str):
        # 从注册表中获取 PromptLib 实例
        self._lib: PromptLib = get_lib(lib_id)
        self._lib_id: str = lib_id

    @property
    def lib_id(self) -> str:
        return self._lib_id

    # ── prompt 方法代理 ────────────────────────────────────────────────────────

    def build_system(self, session: "Session", model: str, language: str, **kwargs) -> list:
        """构建 system prompt 列表，透传给底层 PromptLib。"""
        return self._lib.build_system(session, model, language, **kwargs)

    def on_message(self, message: dict, session: "Session", history: list, **kwargs) -> dict:
        """消息写入 history 前的统一处理入口，透传给底层 PromptLib。"""
        return self._lib.on_message(message, session, history, **kwargs)

    def build_compact_messages(self, summary: str, transcript_ref: str) -> list:
        """LLM 压缩完成后写回 history 的消息格式。"""
        return self._lib.build_compact_messages(summary, transcript_ref)

    # ── ccserver 工具构建（原 PromptLib.build_tools，此处实现避免 prompts_lib 反向依赖）──

    def build_tools(
        self,
        session: "Session",
        adapter: "ModelAdapter",
        settings: "ProjectSettings",
        emitter: "BaseEmitter | None" = None,
        model: str = "",
    ) -> dict:
        """
        构建该 prompt lib 对应的内置工具字典。

        优先调用 PromptLib 子类的 build_tools（允许自定义工具集），
        若子类未覆盖则走此处的默认实现。

        默认实现包含全套 ccserver 内置工具，子类可覆盖以裁剪或扩展。
        """
        # 子类覆盖了 build_tools：直接委托
        if hasattr(type(self._lib), 'build_tools') and 'build_tools' in type(self._lib).__dict__:
            return self._lib.build_tools(session, adapter, settings, emitter=emitter, model=model)

        # 默认实现：构建全套 ccserver 内置工具
        return self._build_default_tools(session, adapter, settings, emitter=emitter, model=model)

    def patch_tool_schemas(self, schemas: list[dict]) -> list[dict]:
        """对工具 schema 列表做后处理，透传给底层 PromptLib。"""
        return self._lib.patch_tool_schemas(schemas)

    def _build_default_tools(
        self,
        session: "Session",
        adapter: "ModelAdapter",
        settings: "ProjectSettings",
        emitter: "BaseEmitter | None" = None,
        model: str = "",
    ) -> dict:
        """
        默认工具集构建逻辑（原 PromptLib.build_tools 默认实现）。

        WebSearch 选择策略（同一时刻只注册一个）：
          - lib_id == "cc_reverse:v2.1.81" 且 adapter 是 AnthropicAdapter → Anthropic BTWebSearch
          - 其余情况 → DuckDuckGo BTDDGWebSearch（不依赖 Anthropic）
        """
        from ccserver.builtins.tools import (
            BTBash, BTRead, BTWrite, BTEdit, BTGlob, BTGrep,
            BTCompact, BTTaskCreate, BTTaskUpdate, BTTaskGet,
            BTTaskList, BTTaskStop, BTAskUser, BTWebFetch,
            BTWebSearch, BTDDGWebSearch, BTAgent, BTSendMessage,
        )
        from ccserver.model import AnthropicAdapter

        # 基础工具（不需要 LLM client）
        tools: dict = {
            "Bash":       BTBash(session.project_root, settings, session=session, emitter=emitter),
            "Read":       BTRead(session.project_root),
            "Write":      BTWrite(session.project_root),
            "Edit":       BTEdit(session.project_root),
            "Glob":       BTGlob(session.project_root),
            "Grep":       BTGrep(session.project_root),
            "Compact":    BTCompact(),
            "TaskCreate": BTTaskCreate(session.tasks),
            "TaskUpdate": BTTaskUpdate(session.tasks),
            "TaskGet":    BTTaskGet(session.tasks),
            "TaskList":   BTTaskList(session.tasks),
            "TaskStop":   BTTaskStop(session.shell_tasks, session=session),
            "AskUser":    BTAskUser(),
        }

        # 需要 LLM client 的工具
        if adapter is not None:
            tools["WebFetch"] = BTWebFetch(adapter)

            # WebSearch 选择逻辑：同一时刻只注册一个
            if self._lib_id == "cc_reverse:v2.1.81" and isinstance(adapter, AnthropicAdapter):
                tools["WebSearch"] = BTWebSearch(adapter)
            else:
                tools["WebSearch"] = BTDDGWebSearch(adapter)

        # Agent 工具动态注入
        tools["Agent"] = BTAgent(agent_catalog=session.agents.build_catalog())

        # Agent Team 通信工具（仅在开启 team 功能时注册）
        if session.settings.user_agent_team:
            tools["SendMessage"] = BTSendMessage()

        # 定时任务工具（始终注册）
        from ccserver.managers.cron.tools import build_cron_tools
        tools.update(build_cron_tools(session.cron_scheduler))

        # 系统操作层工具
        from ccserver.builtins.tools import BTScreenCapture, BTInputClick, BTInputType
        tools["ScreenCapture"] = BTScreenCapture()
        tools["InputClick"]    = BTInputClick()
        tools["InputType"]     = BTInputType()

        # AI 理解层工具（extra，按需注册）
        try:
            from ccserver.extra_tools.vision import BTScreenFind
            tools["ScreenFind"] = BTScreenFind(session=session, adapter=adapter, model=model)
        except ImportError:
            pass

        return tools
