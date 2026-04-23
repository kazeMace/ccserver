from .base import BuiltinTools, ToolParam, ToolResult


class BTTaskList(BuiltinTools):

    name = "TaskList"

    description = (
        "List all active tasks for the current session in a compact summary. "
        "Shows each task's ID, subject, and status. "
        "Deleted tasks are excluded. "
        "Use this to get an overview of pending and in-progress work, "
        "or to look up a task ID before calling TaskGet or TaskUpdate."
    )

    params = {}

    def __init__(self, task_manager):
        self._tasks = task_manager

    async def run(self) -> ToolResult:
        rendered = self._tasks.render_list()
        return ToolResult.ok(rendered)
