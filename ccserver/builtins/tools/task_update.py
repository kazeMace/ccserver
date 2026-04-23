from .base import BuiltinTools, ToolParam, ToolResult


class BTTaskUpdate(BuiltinTools):

    name = "TaskUpdate"

    description = (
        "Update an existing task's status, subject, or description. "
        "Only pass the fields you want to change — omitted fields stay unchanged. "
        "Workflow: set status='in_progress' when starting a task, "
        "'completed' when the work is fully done, 'deleted' to remove it from the list. "
        "Do NOT mark a task 'completed' if tests are failing or implementation is partial."
    )

    params = {
        "task_id": ToolParam(
            type="string",
            description=(
                "The ID of the task to update. "
                "Use TaskList to look up IDs if you don't have them."
            ),
        ),
        "status": ToolParam(
            type="string",
            description=(
                "New status. "
                "'pending' = not started yet; "
                "'in_progress' = currently being worked on (set this before starting); "
                "'completed' = fully done; "
                "'deleted' = permanently remove from the list."
            ),
            required=False,
            enum=["pending", "in_progress", "completed", "deleted"],
        ),
        "subject": ToolParam(
            type="string",
            description="New short title for the task (replaces the existing subject).",
            required=False,
        ),
        "description": ToolParam(
            type="string",
            description="New detailed description (replaces the existing description).",
            required=False,
        ),
    }

    def __init__(self, task_manager):
        self._tasks = task_manager

    async def run(
        self,
        task_id: str,
        status: str = None,
        subject: str = None,
        description: str = None,
    ) -> ToolResult:
        try:
            task = self._tasks.update(
                task_id=task_id,
                status=status,
                subject=subject,
                description=description,
            )
            return ToolResult.ok(
                f"Task #{task.id} updated.\n"
                f"Subject: {task.subject}\n"
                f"Status: {task.status}"
            )
        except ValueError as e:
            return ToolResult.error(str(e))
