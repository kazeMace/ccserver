"""
tests/test_bash_shell_task_integration.py — BTBash 后台任务与 Session 集成测试。

覆盖：
  - BTBash.__init__ 接受 session 参数，通过 session.shell_tasks 注册任务
  - shell_tasks 为 None 时不阻断后台执行（仅警告）
  - Session.shell_tasks lazy property 正确初始化
  - 后台任务注册到 Session.shell_tasks，task_started 事件发出
  - 命令失败时自动标记 failed
  - 前台路径不受影响
"""

import pytest
import anyio
import asyncio
from pathlib import Path
from datetime import datetime

from ccserver.session import Session
from ccserver.tasks import (
    ShellTaskState,
    ShellTaskRegistry,
    generate_shell_id,
    TaskStatus,
)
from ccserver.builtins.tools.bash import BTBash


class MockSettings:
    def is_command_allowed(self, tool: str, cmd: str) -> bool:
        return True

    denied_commands: dict = {}
    allowed_commands: dict = {}


class TestBTBashInit:
    """BTBash 构造函数测试。"""

    def test_accepts_session_parameter(self):
        """BTBash.__init__ 应接受可选的 session 和 emitter 参数。"""
        session = Session(
            id="test-init-session",
            workdir=Path("/tmp"),
            project_root=Path("/tmp"),
        )
        bash = BTBash(
            workdir=session.project_root,
            settings=MockSettings(),
            session=session,
            emitter=None,
        )
        assert bash._session is session
        # shell_tasks 通过 session 访问
        assert bash._shell_tasks is session.shell_tasks
        # emitter 未注入时为 None
        assert bash._emitter is None

    def test_accepts_emitter_parameter(self):
        """BTBash.__init__ 应接受 emitter 参数并正确存储。"""
        # 构造一个 mock emitter
        class MockEmitter:
            async def emit_task_started(self, **kw): pass
            async def emit_task_done(self, **kw): pass
            async def emit(self, **kw): pass

        mock_emitter = MockEmitter()
        bash = BTBash(
            workdir=Path("/tmp"),
            settings=MockSettings(),
            session=None,
            emitter=mock_emitter,
        )
        assert bash._emitter is mock_emitter

    def test_session_defaults_to_none(self):
        """session 和 emitter 均不传时默认为 None。"""
        bash = BTBash(workdir=Path("/tmp"), settings=MockSettings())
        assert bash._session is None
        assert bash._shell_tasks is None
        assert bash._emitter is None


class TestSessionShellTasks:
    """Session.shell_tasks 属性测试。"""

    def test_shell_tasks_lazy_init(self):
        """shell_tasks 应在首次访问时懒创建。"""
        session = Session(
            id="test-shell-tasks",
            workdir=Path("/tmp"),
            project_root=Path("/tmp"),
        )
        assert isinstance(session.shell_tasks, ShellTaskRegistry)

    def test_shell_tasks_same_instance(self):
        """多次访问返回同一实例。"""
        session = Session(
            id="test-shell-tasks-2",
            workdir=Path("/tmp"),
            project_root=Path("/tmp"),
        )
        r1 = session.shell_tasks
        r2 = session.shell_tasks
        assert r1 is r2

    def test_session_has_shell_tasks_field(self):
        """_shell_tasks 字段在 dataclass 中正确声明。"""
        session = Session(
            id="test-shell-tasks-3",
            workdir=Path("/tmp"),
            project_root=Path("/tmp"),
        )
        assert hasattr(session, "_shell_tasks")
        assert isinstance(session._shell_tasks, ShellTaskRegistry)


class TestBTBashBackgroundIntegration:
    """BTBash 后台任务完整生命周期集成测试。"""

    @pytest.mark.anyio
    async def test_background_task_registered_to_shell_tasks(self):
        """run_in_background=True 应将任务注册到 shell_tasks。"""
        session = Session(
            id="test-bg-integration",
            workdir=Path("/tmp"),
            project_root=Path("/tmp"),
        )

        bash = BTBash(
            workdir=session.workdir,
            settings=MockSettings(),
            session=session,
        )

        # 执行后台任务：sleep 后自动完成
        result = await bash.run(
            command="sleep 0.2 && echo done",
            description="test sleep",
            run_in_background=True,
        )

        # 返回值应包含 task_id
        assert "task_id=" in result.content
        assert result.is_error is False

        # 任务已注册
        task_id = result.content.split("task_id=")[1].split(",")[0]
        assert task_id.startswith("b"), f"task_id should start with 'b', got {task_id}"
        assert session.shell_tasks.count() == 1

        # 等待任务完成
        for _ in range(30):
            await asyncio.sleep(0.1)
            task = session.shell_tasks.get(task_id)
            if task and task.is_done:
                break

        task = session.shell_tasks.get(task_id)
        assert task is not None
        assert task.is_done, f"task should be done, status={task.status}"
        assert task.exit_code == 0
        assert "done" in task.output

    @pytest.mark.anyio
    async def test_background_without_session_still_runs(self):
        """session=None 时，后台任务仍正常启动（仅警告）。"""
        bash = BTBash(
            workdir=Path("/tmp"),
            settings=MockSettings(),
            session=None,
        )

        result = await bash.run(
            command="sleep 0.2 && echo ok",
            run_in_background=True,
        )

        assert result.is_error is False
        assert "pid=" in result.content
        await asyncio.sleep(0.3)

    @pytest.mark.anyio
    async def test_background_failed_command_marks_failed(self):
        """命令 exit_code != 0 时应标记为 failed。"""
        session = Session(
            id="test-bg-fail",
            workdir=Path("/tmp"),
            project_root=Path("/tmp"),
        )

        bash = BTBash(
            workdir=session.workdir,
            settings=MockSettings(),
            session=session,
        )

        result = await bash.run(
            command="exit 42",
            run_in_background=True,
        )

        task_id = result.content.split("task_id=")[1].split(",")[0]

        # 等待任务完成
        for _ in range(30):
            await asyncio.sleep(0.1)
            task = session.shell_tasks.get(task_id)
            if task and task.is_done:
                break

        task = session.shell_tasks.get(task_id)
        assert task.status == TaskStatus.FAILED
        assert task.exit_code == 42
        assert "exit code 42" in (task.reason or "")

    @pytest.mark.anyio
    async def test_foreground_still_works(self):
        """run_in_background=False（前台）路径未受影响。"""
        session = Session(
            id="test-foreground",
            workdir=Path("/tmp"),
            project_root=Path("/tmp"),
        )

        bash = BTBash(
            workdir=session.workdir,
            settings=MockSettings(),
            session=session,
        )

        result = await bash.run(command="echo hello world")
        assert result.content == "hello world"
        assert result.is_error is False

    @pytest.mark.anyio
    async def test_foreground_non_zero_exit_is_error(self):
        """前台命令 exit_code != 0 应返回 is_error=True。"""
        session = Session(
            id="test-fg-err",
            workdir=Path("/tmp"),
            project_root=Path("/tmp"),
        )

        bash = BTBash(
            workdir=session.workdir,
            settings=MockSettings(),
            session=session,
        )

        result = await bash.run(command="echo 'failed'; exit 5")
        assert result.is_error is True
        assert "failed" in result.content or "5" in result.content
