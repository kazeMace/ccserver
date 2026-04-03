from .bt_base import BaseTool, ToolParam, ToolResult


class BTTaskCreate(BaseTool):

    name = "TaskCreate"

    description = (
        "Create a new task to track progress on one step of a complex, multi-step job. "
        "Use this at the start of non-trivial work to break it into trackable units — "
        "do not create tasks for single trivial actions. "
        "Returns the newly created task including its assigned ID, "
        "which you must pass to TaskUpdate and TaskGet."
    )

    params = {
        "subject": ToolParam(
            type="string",
            description=(
                "Short imperative title for the task (1-10 words). "
                "Examples: 'Fix authentication bug in login flow', "
                "'Add pagination to user list API', 'Write unit tests for parser'."
            ),
        ),
        "description": ToolParam(
            type="string",
            description=(
                "Detailed description of what needs to be done and the acceptance criteria. "
                "Be specific enough that the task is self-explanatory when read later."
            ),
        ),
    }

    def __init__(self, task_manager):
        self._tasks = task_manager

    async def run(self, subject: str, description: str) -> ToolResult:
        try:
            task = self._tasks.create(subject=subject, description=description)
            return ToolResult.ok(
                f"Task #{task.id} created.\n"
                f"Subject: {task.subject}\n"
                f"Status: {task.status}"
            )
        except ValueError as e:
            return ToolResult.error(str(e))
