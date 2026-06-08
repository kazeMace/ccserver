from typing import TYPE_CHECKING

from ..base import BuiltinTools, ToolResult

if TYPE_CHECKING:
    from ccserver.managers.tasks import TaskManager


class BTTaskList(BuiltinTools):

    name = "TaskList"
    risk = "low"
    tags = ["task"]

    description = (
        "List all active tasks for the current session in a compact summary. "
        "Shows each task's ID, subject, and status. "
        "Deleted tasks are excluded. "
        "Use this to get an overview of pending and in-progress work, "
        "or to look up a task ID before calling TaskGet or TaskUpdate."
    )

    params = {}

    def __init__(self, task_manager: "TaskManager"):
        self._tasks = task_manager

    async def run(self) -> ToolResult:
        rendered = self._tasks.render_list()
        return ToolResult.ok(rendered)
