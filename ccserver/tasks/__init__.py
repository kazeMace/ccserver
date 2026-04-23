"""
tasks — 后台任务状态管理层。

本包对齐 Claude Code 的 Background Task 设计（第五章）。
所有在 Session 内运行的后台任务（shell / agent / remote 等）均在此层定义状态结构和注册逻辑。

当前实现：
    shell.py    — local_bash 后台任务（ShellTaskState + ShellTaskRegistry）
    agent.py    — local_agent 后台任务（AgentTaskState + AgentTaskRegistry）

目录结构：
    ccserver/tasks/
        __init__.py   — 统一导出
        shell.py      — Shell 后台任务状态
        agent.py      — Agent 后台任务状态
"""

from .shell import (
    # 常量
    SHELL_TASK_PREFIX,
    TaskStatus,
    # 工具函数
    generate_shell_id,
    is_shell_task_state,
    # 数据结构
    ShellTaskState,
    # 注册表
    ShellTaskRegistry,
)

from .agent import (
    # 常量
    AGENT_TASK_PREFIX,
    AgentTaskStatus,
    # 工具函数
    generate_agent_id,
    # 数据结构
    AgentTaskState,
    # 注册表
    AgentTaskRegistry,
)

__all__ = [
    # shell
    "SHELL_TASK_PREFIX",
    "TaskStatus",
    "generate_shell_id",
    "is_shell_task_state",
    "ShellTaskState",
    "ShellTaskRegistry",
    # agent
    "AGENT_TASK_PREFIX",
    "AgentTaskStatus",
    "generate_agent_id",
    "AgentTaskState",
    "AgentTaskRegistry",
]
