"""
builtins/tools/task_stop.py — TaskStop 工具：主动终止后台 Shell 任务。

Agent 通过此工具停止 run_in_background=True 的 Bash 命令。
对应 Claude Code 的 TaskStopTool（第五章 Background Task 系统）。

注意：此工具只终止 Shell 后台任务（local_bash），
不支持终止 Agent 后台任务（local_agent），
Agent 任务通过 AgentScheduler.cancel() 处理。
"""

from typing import TYPE_CHECKING

from .base import BuiltinTools, ToolParam, ToolResult

if TYPE_CHECKING:
    from ccserver.session import Session
    from ccserver.tasks import ShellTaskRegistry


class BTTaskStop(BuiltinTools):

    name = "TaskStop"

    description = (
        "Stop a running background task by its task ID. "
        "Use this to terminate long-running commands that are no longer needed, "
        "such as dev servers, watchers, or builds that are stuck or consuming resources. "
        "Only tasks started with run_in_background=true have a task_id "
        "(shown in the Bash tool result as 'task_id=b...'). "
        "Returns success only if the task was running at the time of the request."
    )

    params = {
        "task_id": ToolParam(
            type="string",
            description=(
                "The background task ID to stop. "
                "This is the 'task_id' returned by Bash when run_in_background=true, "
                "formatted as 'b' followed by 8 hex characters (e.g., 'b3f2a1c0'). "
                "You can find running task IDs via TaskList or by checking the Bash tool result."
            ),
        ),
    }

    def __init__(
        self,
        shell_tasks: "ShellTaskRegistry",
        session: "Session | None" = None,
    ):
        """
        Args:
            shell_tasks: Session 级别的 ShellTaskRegistry，用于 kill 任务。
            session:     Session 引用（用于获取 agent_id 填充 reason）。
                        不传则 reason 不包含 agent 信息。
        """
        self._shell_tasks = shell_tasks
        self._session = session

    async def run(self, task_id: str) -> ToolResult:
        # 检查任务是否存在
        task = self._shell_tasks.get(task_id)
        if task is None:
            return ToolResult.error(
                f"Task '{task_id}' not found. "
                "Use TaskList to see available running task IDs."
            )

        # 检查任务状态
        if not task.is_running:
            return ToolResult.error(
                f"Task '{task_id}' is '{task.status}', not running. "
                f"Only running tasks can be stopped. "
                f"Current status: {task.status}."
            )

        # 获取调用者 agent 信息（用于 reason 记录）
        reason = self._build_reason()

        # 执行终止
        ok = self._shell_tasks.kill(task_id, reason=reason)
        if not ok:
            return ToolResult.error(
                f"Failed to stop task '{task_id}'. "
                "The task may have completed between the status check and the stop request."
            )

        return ToolResult.ok(
            f"Task '{task_id}' stopped. "
            f"Command: {task.command[:80]} "
            f"(pid={task.pid})."
        )

    def _build_reason(self) -> str:
        """构造终止原因字符串，供日志和审计使用。"""
        parts = ["task_stop_tool"]
        if self._session is not None:
            parts.append(f"session={self._session.id[:8]}")
        return " | ".join(parts)
