from .base import BuiltinTools, ToolParam, ToolResult


class BTTaskGet(BuiltinTools):

    name = "TaskGet"

    description = (
        "Retrieve the full details of a single task by its ID. "
        "Use this when you need the complete description of a task before starting work on it, "
        "or to verify a task's current status. "
        "Returns the task's ID, subject, description, and status. "
        "Use TaskList first if you need to find the task ID."
    )

    params = {
        "task_id": ToolParam(
            type="string",
            description=(
                "The ID of the task to retrieve. "
                "Task IDs are integers returned by TaskCreate and shown in TaskList."
            ),
        ),
    }

    def __init__(self, task_manager):
        self._tasks = task_manager

    async def run(self, task_id: str) -> ToolResult:
        try:
            task = self._tasks.get(task_id)
            return ToolResult.ok(
                f"Task #{task.id}\n"
                f"Subject: {task.subject}\n"
                f"Description: {task.description}\n"
                f"Status: {task.status}"
            )
        except ValueError as e:
            return ToolResult.error(str(e))
