"""
tests/test_shell_task.py — ShellTaskState 与 ShellTaskRegistry 单元测试。

覆盖：
  - generate_shell_id() 唯一性与前缀
  - ShellTaskState 状态流转（pending → running → completed/failed/killed）
  - append_output 增量追加
  - to_dict / from_dict 序列化
  - is_shell_task_state 类型守卫
  - ShellTaskRegistry 注册/查询/终止/驱逐
  - ShellTaskRegistry.summary() 统计

注意：所有涉及 asyncio.Future 的测试使用 @pytest.mark.anyio，
      anyio.run() 在 pytest 的 anyio plugin 上下文中自动管理事件循环。
"""

import pytest
from ccserver.tasks import (
    generate_shell_id,
    is_shell_task_state,
    ShellTaskState,
    ShellTaskRegistry,
    TaskStatus,
    SHELL_TASK_PREFIX,
)


class TestGenerateShellId:
    """ID 生成测试（同步，不需要事件循环）。"""

    def test_id_starts_with_b(self):
        """ID 必须以 'b' 前缀开头，对应 local_bash 类型。"""
        tid = generate_shell_id()
        assert tid.startswith(SHELL_TASK_PREFIX)

    def test_id_length(self):
        """ID 长度为 9 位：1 位前缀 + 8 位 hex。"""
        tid = generate_shell_id()
        assert len(tid) == 9

    def test_id_unique(self):
        """两次调用应生成不同的 UUID，不会碰撞。"""
        ids = {generate_shell_id() for _ in range(100)}
        assert len(ids) == 100, "100 次调用应产生 100 个不同 ID"


class TestShellTaskStateLifecycle:
    """任务状态流转测试（需要事件循环）。"""

    @pytest.mark.anyio
    async def test_initial_pending(self):
        """新创建的 task 默认为 pending。"""
        task = ShellTaskState(id="b123", command="echo hi")
        assert task.status == TaskStatus.PENDING
        assert task.is_running is False
        assert task.is_done is False
        assert task.is_success is False
        assert task.is_backgrounded is True

    @pytest.mark.anyio
    async def test_mark_running_sets_pid_and_time(self):
        """mark_running 应填充 pid 和 start_time。"""
        task = ShellTaskState(id="b123", command="sleep 10")
        # proc_started 在 dataclass 初始化时已创建
        task.mark_running(pid=12345, proc="proc_obj")
        assert task.status == TaskStatus.RUNNING
        assert task.pid == 12345
        assert task.start_time is not None
        assert task.is_running is True

    @pytest.mark.anyio
    async def test_mark_running_asserts_on_wrong_state(self):
        """已在 running 状态时再次 mark_running 应抛 AssertionError。"""
        task = ShellTaskState(id="b123", command="sleep 10")
        task.mark_running(pid=1, proc="p")
        with pytest.raises(AssertionError):
            task.mark_running(pid=2, proc="p2")

    @pytest.mark.anyio
    async def test_mark_completed_sets_exit_code_and_end_time(self):
        """mark_completed 应填充 exit_code、end_time 并清理 proc。"""
        task = ShellTaskState(id="b123", command="echo hi")
        task.mark_running(pid=99, proc="proc_ref")
        assert task.proc is not None

        task.mark_completed(exit_code=0)
        assert task.exit_code == 0
        assert task.status == TaskStatus.COMPLETED
        assert task.end_time is not None
        assert task.is_done is True
        assert task.is_success is True
        assert task.proc is None  # 已清理

    @pytest.mark.anyio
    async def test_mark_failed_sets_reason(self):
        """mark_failed 应设置 exit_code != 0 并记录 reason。"""
        task = ShellTaskState(id="b123", command="exit 1")
        task.mark_running(pid=99, proc="p")
        task.mark_failed(exit_code=1, reason="command returned 1")
        assert task.exit_code == 1
        assert task.status == TaskStatus.FAILED
        assert task.reason == "command returned 1"
        assert task.is_success is False

    @pytest.mark.anyio
    async def test_mark_killed_kills_proc(self):
        """mark_killed 应发送 kill 信号并标记状态。"""
        task = ShellTaskState(id="b123", command="sleep 60")

        # 构造一个 mock proc，带 kill 方法
        class MockProc:
            killed = False

            def kill(self):
                self.killed = True

        mock_proc = MockProc()
        task.mark_running(pid=999, proc=mock_proc)
        task.mark_killed(reason="user requested")

        assert task.status == TaskStatus.KILLED
        assert mock_proc.killed is True
        assert task.reason == "user requested"


class TestAppendOutput:
    """增量输出追加测试（同步）。"""

    def test_append_output_increments_offset(self):
        """append_output 追加后，offset 指向本次追加前的长度（即新内容的起始位置）。"""
        task = ShellTaskState(id="b123", command="echo hi")
        task.append_output("hello ")
        assert task.output == "hello "
        assert task.output_offset == 0  # 追加前 output 为空

        task.append_output("world")
        assert task.output == "hello world"
        assert task.output_offset == 6  # 本次追加前 "hello " 的长度

    def test_append_output_empty_chunk(self):
        """空字符串追加不应破坏 offset。"""
        task = ShellTaskState(id="b123", command="echo hi")
        task.append_output("abc")
        task.append_output("")
        assert task.output == "abc"
        assert task.output_offset == 3


class TestSerialization:
    """序列化 / 反序列化测试。"""

    @pytest.mark.anyio
    async def test_to_dict_fields(self):
        """to_dict 应包含所有公开字段。"""
        task = ShellTaskState(id="babc1234", command="ls", description="list dir")
        task.mark_running(pid=42, proc="p")
        task.append_output("file1\nfile2\n")
        task.mark_completed(exit_code=0)
        d = task.to_dict()

        assert d["id"] == "babc1234"
        assert d["type"] == "local_bash"
        assert d["command"] == "ls"
        assert d["description"] == "list dir"
        assert d["status"] == TaskStatus.COMPLETED
        assert d["pid"] == 42
        assert d["output"] == "file1\nfile2\n"
        assert d["exit_code"] == 0
        assert d["start_time"] is not None
        assert d["end_time"] is not None

    @pytest.mark.anyio
    async def test_from_dict_restores_fields(self):
        """from_dict 应正确恢复所有字段（proc 除外）。"""
        task = ShellTaskState(id="b2", command="pwd")
        task.mark_running(pid=1, proc="proc")
        task.mark_completed(exit_code=0)

        d = task.to_dict()
        restored = ShellTaskState.from_dict(d)

        assert restored.id == task.id
        assert restored.command == task.command
        assert restored.status == task.status
        assert restored.exit_code == task.exit_code
        assert restored.output == task.output
        # proc 在 from_dict 后必为 None（无法恢复进程引用）
        assert restored.proc is None


class TestTypeGuard:
    """is_shell_task_state 类型守卫测试（同步）。"""

    def test_returns_true_for_instance(self):
        """ShellTaskState 实例应返回 True。"""
        task = ShellTaskState(id="b1", command="echo hi")
        assert is_shell_task_state(task) is True

    def test_returns_false_for_others(self):
        """其他类型应返回 False。"""
        assert is_shell_task_state("not a task") is False
        assert is_shell_task_state(None) is False
        assert is_shell_task_state({"id": "b1"}) is False


class TestShellTaskRegistry:
    """ShellTaskRegistry 注册表测试。"""

    @pytest.mark.anyio
    async def test_register_and_get(self):
        """register 后 get 应能取回同一对象。"""
        reg = ShellTaskRegistry()
        task = ShellTaskState(id=generate_shell_id(), command="echo hi")
        reg.register(task)
        assert reg.get(task.id) is task
        assert reg.count() == 1

    @pytest.mark.anyio
    async def test_register_duplicate_raises(self):
        """重复注册同一 task_id 应抛 ValueError。"""
        reg = ShellTaskRegistry()
        task = ShellTaskState(id="bduplicate", command="echo hi")
        reg.register(task)
        with pytest.raises(ValueError, match="already registered"):
            reg.register(task)

    @pytest.mark.anyio
    async def test_list_running_filters_correctly(self):
        """list_running 仅返回 is_running == True 的任务。"""
        reg = ShellTaskRegistry()

        t1 = ShellTaskState(id="b1", command="sleep 10")
        t2 = ShellTaskState(id="b2", command="sleep 10")
        t1.mark_running(pid=1, proc="p")

        reg.register(t1)
        reg.register(t2)

        running = reg.list_running()
        assert len(running) == 1
        assert running[0].id == "b1"

    @pytest.mark.anyio
    async def test_kill_running_task(self):
        """kill 应成功并改变状态。"""
        reg = ShellTaskRegistry()

        class MockProc:
            killed = False

            def kill(self):
                self.killed = True

        task = ShellTaskState(id="bkill", command="sleep 60")
        task.mark_running(pid=1, proc=MockProc())
        reg.register(task)

        ok = reg.kill(task.id, reason="test")
        assert ok is True
        assert task.status == TaskStatus.KILLED
        assert task.reason == "test"

    @pytest.mark.anyio
    async def test_kill_nonexistent_returns_false(self):
        """kill 不存在的 task_id 返回 False。"""
        reg = ShellTaskRegistry()
        assert reg.kill("bnotexist") is False

    @pytest.mark.anyio
    async def test_evict_requires_done_state(self):
        """evict 仅对已完成任务生效，running 状态拒绝。"""
        reg = ShellTaskRegistry()

        task = ShellTaskState(id="bevict", command="sleep 10")
        task.mark_running(pid=1, proc="p")
        reg.register(task)

        # running 状态不能 evict
        assert reg.evict(task.id) is False
        assert task.id in reg._tasks  # 仍在注册表中

        task.mark_completed(exit_code=0)
        assert reg.evict(task.id) is True
        assert reg.count() == 0  # 任务已从注册表驱逐

    @pytest.mark.anyio
    async def test_evict_done_tasks_batch(self):
        """evict_done_tasks 批量清理所有已完成任务。"""
        reg = ShellTaskRegistry()

        for i in range(3):
            t = ShellTaskState(id=f"b{i}", command=f"echo {i}")
            t.mark_running(pid=i, proc="p")
            t.mark_completed(exit_code=0)
            reg.register(t)

        # 再加一个 running 的
        tr = ShellTaskState(id="br", command="sleep 10")
        tr.mark_running(pid=99, proc="p")
        reg.register(tr)

        evicted = reg.evict_done_tasks()
        assert evicted == 3
        assert reg.count() == 1
        assert reg.get("br") is not None

    @pytest.mark.anyio
    async def test_summary_counts(self):
        """summary 返回正确的各状态计数。"""
        reg = ShellTaskRegistry()

        # pending + running
        t1 = ShellTaskState(id="b1", command="sleep 10")
        reg.register(t1)
        t1.mark_running(pid=1, proc="p")

        # completed
        t2 = ShellTaskState(id="b2", command="exit 0")
        t2.mark_running(pid=2, proc="p")
        t2.mark_completed(exit_code=0)
        reg.register(t2)

        # failed
        t3 = ShellTaskState(id="b3", command="exit 1")
        t3.mark_running(pid=3, proc="p")
        t3.mark_failed(exit_code=1, reason="bad exit")
        reg.register(t3)

        s = reg.summary()
        assert s["total"] == 3
        assert s["running"] == 1
        assert s["completed"] == 1
        assert s["failed"] == 1
        assert s["killed"] == 0
        assert s["evicted"] == 0
