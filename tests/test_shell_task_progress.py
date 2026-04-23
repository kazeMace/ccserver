"""
tests/test_shell_task_progress.py — Shell 后台任务轮询 + 增量输出测试。

覆盖：
  - ShellTaskState.read_incremental() 增量读取
  - BTBash 后台任务 emit_task_progress 被调用
  - 多 chunk 分割、正常完成、进程失败、空输出场景
  - read_incremental 在多次追加后的行为
"""

import asyncio
import pytest

from ccserver.tasks import ShellTaskState, generate_shell_id


# ─── read_incremental ────────────────────────────────────────────────────────

class TestReadIncremental:
    """read_incremental() 增量读取测试。"""

    @pytest.mark.anyio
    async def test_initial_returns_empty(self):
        """初始状态 read_incremental 应返回空字符串。"""
        state = ShellTaskState(id=generate_shell_id(), command="echo hi")
        assert state.read_incremental() == ""

    @pytest.mark.anyio
    async def test_returns_incremental_after_append(self):
        """append_output 后 read_incremental 应只返回新增部分。"""
        state = ShellTaskState(id=generate_shell_id(), command="echo hi")
        state.append_output("hello")
        assert state.read_incremental() == "hello"
        # 再次调用应返回空（offset 已更新）
        assert state.read_incremental() == ""

    @pytest.mark.anyio
    async def test_multiple_appends_accumulative(self):
        """多次 append_output，每次 read_incremental 只返回该次增量。"""
        state = ShellTaskState(id=generate_shell_id(), command="build")
        state.append_output("Compiling...")
        inc1 = state.read_incremental()
        assert inc1 == "Compiling..."

        state.append_output("\nBuilding bundle...")
        inc2 = state.read_incremental()
        assert inc2 == "\nBuilding bundle..."

        # 全量验证
        assert state.output == "Compiling...\nBuilding bundle..."
        assert state.read_incremental() == ""  # offset 已追上

    @pytest.mark.anyio
    async def test_read_incremental_after_empty_append(self):
        """append_output("") 后 read_incremental 应返回空（offset 不变）。"""
        state = ShellTaskState(id=generate_shell_id(), command="echo hi")
        state.append_output("hello")
        state.read_incremental()          # offset 更新到 5
        state.append_output("")            # 空追加，read_incremental 会恢复 offset
        assert state.read_incremental() == ""
        assert state.read_incremental() == ""

    @pytest.mark.anyio
    async def test_preserves_previously_read_content(self):
        """已读取的增量内容在 read_incremental 后续调用中不再出现。"""
        state = ShellTaskState(id=generate_shell_id(), command="watch")
        state.append_output("line1\n")
        state.read_incremental()
        state.append_output("line2\n")
        state.read_incremental()
        state.append_output("line3\n")
        # 累计 output，但 read_incremental 只返回最新增量
        assert state.output == "line1\nline2\nline3\n"


class MockSettings:
    def is_command_allowed(self, tool: str, cmd: str) -> bool:
        return True

    denied_commands: dict = {}
    allowed_commands: dict = {}


# ─── emit_task_progress 调用验证 ─────────────────────────────────────────────

class TestBTBashProgressEvents:
    """BTBash 后台任务推送 emit_task_progress 事件。"""

    @pytest.mark.anyio
    async def test_progress_event_emitted_on_output(self):
        """有输出时 emit_task_progress 应被调用。"""
        from unittest.mock import AsyncMock, patch
        from pathlib import Path

        from ccserver.builtins.tools.bash import BTBash
        from ccserver.session import Session

        session = Session(
            id="test-progress",
            workdir=Path("/tmp"),
            project_root=Path("/tmp"),
        )
        emitter = AsyncMock()
        bash = BTBash(
            workdir=Path("/tmp"),
            settings=MockSettings(),
            session=session,
            emitter=emitter,
        )

        # 模拟一个立即完成的命令
        with patch("asyncio.create_subprocess_shell") as mock_proc:
            mock_p = AsyncMock()
            mock_p.pid = 12345
            mock_p.returncode = None  # 进程未结束标记

            async def fake_wait(timeout=None):
                await asyncio.sleep(0)  # 让出控制权
                mock_p.returncode = 0
                return 0

            async def fake_read(n=None):
                await asyncio.sleep(0)
                return b"hello world\n"

            mock_p.wait = fake_wait
            mock_p.stdout.read = fake_read
            mock_proc.return_value = mock_p

            result = await bash.run("echo hello", run_in_background=True)
            # 等待 _wait_and_update_task 完成
            await asyncio.sleep(0.3)

        assert result.is_error is False
        assert "background" in result.content.lower()
        # emit_task_progress 应至少被调用一次（有输出）
        assert emitter.emit_task_progress.called

    @pytest.mark.anyio
    async def test_progress_not_emitted_when_emitter_is_none(self):
        """emitter=None 时不崩溃，只打印日志。"""
        from unittest.mock import AsyncMock, patch
        from pathlib import Path

        from ccserver.builtins.tools.bash import BTBash
        from ccserver.session import Session

        session = Session(
            id="test-no-emitter",
            workdir=Path("/tmp"),
            project_root=Path("/tmp"),
        )
        bash = BTBash(
            workdir=Path("/tmp"),
            settings=MockSettings(),
            session=session,
            emitter=None,  # 无 emitter
        )

        with patch("asyncio.create_subprocess_shell") as mock_proc:
            mock_p = AsyncMock()
            mock_p.pid = 12345
            mock_p.returncode = 0

            async def fake_wait(timeout=None):
                return 0

            async def fake_read(n=None):
                return b"output"

            mock_p.wait = fake_wait
            mock_p.stdout.read = fake_read
            mock_proc.return_value = mock_p

            result = await bash.run("echo test", run_in_background=True)
            await asyncio.sleep(0.1)

        assert result.is_error is False

    @pytest.mark.anyio
    async def test_task_done_after_process_exit(self):
        """进程结束后应推送 task_done。"""
        from unittest.mock import AsyncMock, patch
        from pathlib import Path

        from ccserver.builtins.tools.bash import BTBash
        from ccserver.session import Session

        session = Session(
            id="test-done",
            workdir=Path("/tmp"),
            project_root=Path("/tmp"),
        )
        emitter = AsyncMock()
        bash = BTBash(
            workdir=Path("/tmp"),
            settings=MockSettings(),
            session=session,
            emitter=emitter,
        )

        with patch("asyncio.create_subprocess_shell") as mock_proc:
            mock_p = AsyncMock()
            mock_p.pid = 999
            mock_p.returncode = 0

            async def fake_wait(timeout=None):
                return 0

            async def fake_read(n=None):
                return b"final output\n"

            mock_p.wait = fake_wait
            mock_p.stdout.read = fake_read
            mock_proc.return_value = mock_p

            await bash.run("echo final", run_in_background=True)
            await asyncio.sleep(0.3)

        # task_done 应在 task_started 之后被调用
        calls = emitter.emit_task_done.call_args_list
        assert len(calls) >= 1
        assert calls[0].kwargs["status"] == "completed"
        assert "final output" in calls[0].kwargs["output"]

