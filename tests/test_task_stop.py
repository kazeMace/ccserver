"""
tests/test_task_stop.py — TaskStop 工具单元测试。

覆盖：
  - TaskStop 成功终止运行中的任务
  - TaskStop 拒绝不存在的 task_id
  - TaskStop 拒绝已完成的任务
  - TaskStop 拒绝已终止的任务
  - reason 字段正确填充 agent 信息
"""

import pytest
import anyio
from pathlib import Path

from ccserver.session import Session
from ccserver.tasks import ShellTaskState, TaskStatus, generate_shell_id
from ccserver.builtins.tools.task_stop import BTTaskStop


class TestTaskStopSuccess:
    """TaskStop 成功路径测试。"""

    @pytest.mark.anyio
    async def test_kill_running_task(self):
        """TaskStop 应成功终止运行中的任务。"""
        session = Session(
            id="test-task-stop",
            workdir=Path("/tmp"),
            project_root=Path("/tmp"),
        )

        task = ShellTaskState(id=generate_shell_id(), command="sleep 60")

        # mock proc
        class MockProc:
            killed = False

            def kill(self):
                self.killed = True

        task.mark_running(pid=12345, proc=MockProc())
        session.shell_tasks.register(task)
        task_id = task.id

        tool = BTTaskStop(session.shell_tasks, session=session)
        result = await tool.run(task_id=task_id)

        assert result.is_error is False
        assert task_id in result.content
        assert task.status == TaskStatus.KILLED
        assert "12345" in result.content  # pid in output

    @pytest.mark.anyio
    async def test_kill_without_session_info(self):
        """不传入 session 时 reason 不应崩溃。"""
        session = Session(
            id="test-no-session",
            workdir=Path("/tmp"),
            project_root=Path("/tmp"),
        )

        task = ShellTaskState(id=generate_shell_id(), command="sleep 60")

        class MockProc:
            killed = False

            def kill(self):
                self.killed = True

        task.mark_running(pid=1, proc=MockProc())
        session.shell_tasks.register(task)

        tool = BTTaskStop(session.shell_tasks, session=None)
        result = await tool.run(task_id=task.id)
        assert result.is_error is False


class TestTaskStopErrors:
    """TaskStop 错误路径测试。"""

    @pytest.mark.anyio
    async def test_task_not_found(self):
        """不存在的 task_id 应返回错误。"""
        session = Session(
            id="test-not-found",
            workdir=Path("/tmp"),
            project_root=Path("/tmp"),
        )

        tool = BTTaskStop(session.shell_tasks)
        result = await tool.run(task_id="bnotexist")
        assert result.is_error is True
        assert "not found" in result.content

    @pytest.mark.anyio
    async def test_task_already_completed(self):
        """已完成的任务应返回错误。"""
        session = Session(
            id="test-already-done",
            workdir=Path("/tmp"),
            project_root=Path("/tmp"),
        )

        task = ShellTaskState(id=generate_shell_id(), command="echo hi")
        task.mark_running(pid=1, proc="p")
        task.mark_completed(exit_code=0)
        session.shell_tasks.register(task)

        tool = BTTaskStop(session.shell_tasks)
        result = await tool.run(task_id=task.id)
        assert result.is_error is True
        assert task.status in result.content  # 错误消息包含当前状态

    @pytest.mark.anyio
    async def test_task_already_killed(self):
        """已终止的任务应返回错误。"""
        session = Session(
            id="test-already-killed",
            workdir=Path("/tmp"),
            project_root=Path("/tmp"),
        )

        task = ShellTaskState(id=generate_shell_id(), command="sleep 60")

        class MockProc:
            killed = False

            def kill(self):
                self.killed = True

        task.mark_running(pid=1, proc=MockProc())
        task.mark_killed(reason="test")
        session.shell_tasks.register(task)

        tool = BTTaskStop(session.shell_tasks)
        result = await tool.run(task_id=task.id)
        assert result.is_error is True
        assert "not running" in result.content.lower()


class TestTaskStopSchema:
    """TaskStop 工具 schema 测试。"""

    def test_name(self):
        """工具名称必须为 TaskStop。"""
        session = Session(id="test-schema", workdir=Path("/tmp"), project_root=Path("/tmp"))
        tool = BTTaskStop(session.shell_tasks)
        assert tool.name == "TaskStop"

    def test_has_task_id_param(self):
        """必须包含 task_id 参数。"""
        session = Session(id="test-schema2", workdir=Path("/tmp"), project_root=Path("/tmp"))
        tool = BTTaskStop(session.shell_tasks)
        assert "task_id" in tool.params

    def test_schema(self):
        """to_schema() 应生成合法的 Anthropic 工具定义。"""
        session = Session(id="test-schema3", workdir=Path("/tmp"), project_root=Path("/tmp"))
        tool = BTTaskStop(session.shell_tasks)
        schema = tool.to_schema()
        assert schema["name"] == "TaskStop"
        assert "task_id" in schema["input_schema"]["properties"]
