"""
tools — standard tool set for the CCServer agent.

Usage in Agent:
    from ccserver.tools import build_tools

    tools = build_tools(session)
    schemas = [t.to_schema() for t in tools.values()]   # → Anthropic API
    result = await tools["Bash"](command="ls -la")       # → ToolResult
"""

from pathlib import Path
from typing import Any

from .bt_base import BaseTool, ToolParam, ToolResult
from .bt_bash import BTBash
from .bt_read import BTRead
from .bt_write import BTWrite
from .bt_edit import BTEdit
from .bt_glob import BTGlob
from .bt_grep import BTGrep
from .bt_compact import BTCompact, COMPACT_SIGNAL
from .bt_agent import BTAgent, AGENT_SIGNAL
from .bt_task_create import BTTaskCreate
from .bt_task_update import BTTaskUpdate
from .bt_task_get import BTTaskGet
from .bt_task_list import BTTaskList
from .bt_askuser import BTAskUser
from .bt_websearch import BTWebSearch
from .bt_webfetch import BTWebFetch


def build_tools(
    workdir: Path,
    task_manager: Any,
    settings: Any,
    client: Any = None,
) -> dict[str, BaseTool]:
    """
    Instantiate all standard tools for a session.
    Returns a dict keyed by tool name for O(1) dispatch in Agent._handle_tools().

    Args:
        workdir:      Project root directory (session.project_root).
        task_manager: Session-scoped TaskManager instance.
        settings:     ProjectSettings 实例，BTBash 在执行时动态读取 allow/deny。
        client:       AsyncAnthropic client, required for WebSearch and WebFetch.

    Example:
        tools = build_tools(session.project_root, session.tasks, session.settings, client)
        schemas = [t.to_schema() for t in tools.values()]
        result: ToolResult = await tools["Bash"](command="pytest")
    """
    instances: list[BaseTool] = [
        BTBash(workdir, settings),
        BTRead(workdir),
        BTWrite(workdir),
        BTEdit(workdir),
        BTGlob(workdir),
        BTGrep(workdir),
        BTCompact(),
        BTTaskCreate(task_manager),
        BTTaskUpdate(task_manager),
        BTTaskGet(task_manager),
        BTTaskList(task_manager),
        BTAskUser(),
    ]
    if client is not None:
        instances.append(BTWebSearch(client))
        instances.append(BTWebFetch(client))
    return {t.name: t for t in instances}


__all__ = [
    "BaseTool",
    "ToolParam",
    "ToolResult",
    "COMPACT_SIGNAL",
    "AGENT_SIGNAL",
    "build_tools",
    "BTBash",
    "BTRead",
    "BTWrite",
    "BTEdit",
    "BTGlob",
    "BTGrep",
    "BTCompact",
    "BTAgent",
    "BTTaskCreate",
    "BTTaskUpdate",
    "BTTaskGet",
    "BTTaskList",
    "BTAskUser",
    "BTWebSearch",
    "BTWebFetch",
]
