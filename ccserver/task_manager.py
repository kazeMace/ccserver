"""
task_manager — Session 级别的任务管理器。

LLM 通过 TaskCreate/TaskUpdate/TaskGet/TaskList 工具操作任务，
跟踪复杂多步骤任务的执行状态。

与原来的 TodoManager 的区别：
  - Todo 是替换式全量更新（每次调用覆盖整个列表）
  - Task 是增量式操作（独立创建、独立更新，保留历史）
  - Task 有自增 ID，支持按 ID 精确操作
"""


class Task:
    """单条任务记录。"""

    VALID_STATUSES = ("pending", "in_progress", "completed", "deleted")

    def __init__(self, task_id: str, subject: str, description: str):
        self.id = task_id
        self.subject = subject
        self.description = description
        self.status = "pending"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "subject": self.subject,
            "description": self.description,
            "status": self.status,
        }

    def render_line(self) -> str:
        marker = {
            "pending": "[ ]",
            "in_progress": "[>]",
            "completed": "[x]",
            "deleted": "[d]",
        }
        return f"{marker[self.status]} #{self.id}: {self.subject}"


class TaskManager:
    """
    内存中的任务列表管理器，绑定到当前 Session 生命周期。

    任务通过自增整数 ID 标识，支持独立创建和更新。
    重启后任务丢失（内存存储）。
    """

    def __init__(self):
        self._tasks: dict[str, Task] = {}
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return str(self._counter)

    def create(self, subject: str, description: str) -> Task:
        """创建新任务，返回创建后的 Task 对象。"""
        if not subject.strip():
            raise ValueError("subject is required")
        task_id = self._next_id()
        task = Task(task_id=task_id, subject=subject.strip(), description=description.strip())
        self._tasks[task_id] = task
        return task

    def get(self, task_id: str) -> Task:
        """按 ID 获取任务，不存在则抛 ValueError。"""
        task = self._tasks.get(str(task_id))
        if task is None:
            raise ValueError(f"Task #{task_id} not found")
        return task

    def update(self, task_id: str, status: str = None, subject: str = None, description: str = None) -> Task:
        """
        更新任务的一个或多个字段，返回更新后的 Task 对象。
        只传入需要修改的字段，其余保持不变。
        """
        task = self.get(task_id)

        if status is not None:
            if status not in Task.VALID_STATUSES:
                raise ValueError(f"Invalid status '{status}'. Valid values: {Task.VALID_STATUSES}")
            task.status = status

        if subject is not None:
            if not subject.strip():
                raise ValueError("subject cannot be empty")
            task.subject = subject.strip()

        if description is not None:
            task.description = description.strip()

        return task

    def list_all(self) -> list[Task]:
        """返回所有未删除的任务（按创建顺序）。"""
        return [t for t in self._tasks.values() if t.status != "deleted"]

    def render_list(self) -> str:
        """生成给 LLM 看的任务列表字符串。"""
        tasks = self.list_all()
        if not tasks:
            return "No tasks."
        lines = [t.render_line() for t in tasks]
        done = sum(1 for t in tasks if t.status == "completed")
        lines.append(f"\n({done}/{len(tasks)} completed)")
        return "\n".join(lines)
