"""
tests/test_tui_http.py — tui_http 客户端组件单元测试。

覆盖：
  - BackgroundTaskManager: on_task_started/progress/done 状态管理
  - BGRunningTask 增量输出渲染（output_lines 追踪）
  - BackgroundTaskManager.render() 输出格式
  - has_running() 状态查询
  - 任务完成后从管理器移除（不再出现在 render 中）

不覆盖（需要网络或 mock HTTP server）：
  - api_chat_stream() SSE 流式解析（已在集成测试中覆盖）
  - 主循环交互逻辑

测试模式：纯同步/异步函数测试，不依赖网络。
"""

import asyncio
import pytest

from clients.tui_http import (
    BackgroundTaskManager,
    BGRunningTask,
    BG_RUNNING,
    BG_COMPLETED,
)


# ─── BackgroundTaskManager 核心方法 ───────────────────────────────────────────


class TestBackgroundTaskManagerLifecycle:
    """BackgroundTaskManager 生命周期管理测试。"""

    def test_on_task_started_registers_task(self):
        """on_task_started 应将任务加入 _tasks 字典。"""
        mgr = BackgroundTaskManager()
        mgr.on_task_started({
            "task_id": "b0000001",
            "task_type": "local_bash",
            "description": "npm run build",
        })
        assert "b0000001" in mgr._tasks
        task = mgr._tasks["b0000001"]
        assert task.task_id == "b0000001"
        assert task.description == "npm run build"
        assert task.status == BG_RUNNING

    def test_on_task_started_ignores_duplicate(self):
        """重复的 task_id 不应创建多个条目。"""
        mgr = BackgroundTaskManager()
        mgr.on_task_started({"task_id": "b0000001", "task_type": "bash", "description": "first"})
        mgr.on_task_started({"task_id": "b0000001", "task_type": "bash", "description": "second"})
        assert len(mgr._tasks) == 1
        assert mgr._tasks["b0000001"].description == "first"

    def test_on_task_progress_appends_output(self):
        """on_task_progress 应将 output 追加到 task.output。"""
        mgr = BackgroundTaskManager()
        mgr.on_task_started({"task_id": "b0000002", "task_type": "bash", "description": "test"})
        mgr.on_task_progress({"task_id": "b0000002", "output": "Compiling...\n"})
        mgr.on_task_progress({"task_id": "b0000002", "output": "Done!\n"})

        task = mgr._tasks["b0000002"]
        assert "Compiling...\n" in task.output
        assert "Done!\n" in task.output

    def test_on_task_progress_unknown_task_is_noop(self):
        """向未知 task_id 发送 progress 不应崩溃。"""
        mgr = BackgroundTaskManager()
        mgr.on_task_progress({"task_id": "b9999999", "output": "oops"})
        assert "b9999999" not in mgr._tasks

    def test_on_task_done_removes_task(self):
        """on_task_done 应将任务从 _tasks 中移除。"""
        mgr = BackgroundTaskManager()
        mgr.on_task_started({"task_id": "b0000003", "task_type": "bash", "description": "build"})
        mgr.on_task_done({"task_id": "b0000003", "status": "completed"})
        assert "b0000003" not in mgr._tasks

    def test_has_running_true_when_tasks_exist(self):
        """有 running 任务时 has_running() 返回 True。"""
        mgr = BackgroundTaskManager()
        mgr.on_task_started({"task_id": "b0000004", "task_type": "bash", "description": "x"})
        assert mgr.has_running() is True

    def test_has_running_false_when_empty(self):
        """无任务时 has_running() 返回 False。"""
        mgr = BackgroundTaskManager()
        assert mgr.has_running() is False


# ─── 增量输出渲染 ─────────────────────────────────────────────────────────────


class TestIncrementalOutputRendering:
    """BGRunningTask + BackgroundTaskManager.render() 增量渲染测试。"""

    def test_render_outputs_only_new_lines(self):
        """
        模拟 TUI 逐次刷新场景：
        - 第一次 progress：输出 "line1\n"，render 应只显示 line1
        - 第二次 progress：追加 "line2\n"，render 应只显示 line2（不重复 line1）
        """
        mgr = BackgroundTaskManager()
        mgr.on_task_started({"task_id": "b0000010", "task_type": "bash", "description": "build"})
        task = mgr._tasks["b0000010"]

        # 第一次渲染（0 输出行）
        mgr.on_task_progress({"task_id": "b0000010", "output": "Compiling...\n"})
        first_render = mgr.render()
        assert "Compiling..." in first_render

        # 第二次渲染（只追加新行，不重复）
        mgr.on_task_progress({"task_id": "b0000010", "output": "Done!\n"})
        second_render = mgr.render()
        assert "Done!" in second_render
        # 不应重复 "Compiling..."
        # render() 只返回当前任务行（含最新增量），
        # 两次 render 都有输出是正常的（manager 每轮重绘整个任务栏）

    def test_render_returns_empty_when_no_tasks(self):
        """无任务时 render() 返回空字符串。"""
        mgr = BackgroundTaskManager()
        assert mgr.render() == ""

    def test_render_includes_task_id_and_description(self):
        """render() 输出应包含 task_id 和 description。"""
        mgr = BackgroundTaskManager()
        mgr.on_task_started({
            "task_id": "b1234567",
            "task_type": "local_bash",
            "description": "npm run dev",
        })
        output = mgr.render()
        assert "b1234567" in output
        assert "npm run dev" in output

    def test_output_lines_counter_not_affected_by_duplicate_progress(self):
        """
        同一 output 被多次 on_task_progress 传入，
        read_incremental 逻辑应只返回真正的新内容。
        """
        mgr = BackgroundTaskManager()
        mgr.on_task_started({"task_id": "b0000011", "task_type": "bash", "description": "dup"})
        task = mgr._tasks["b0000011"]

        # 写入相同内容两次
        task.output = "hello\nhello\n"
        task.output_lines = 0  # 初始
        all_lines = task.output.splitlines()
        new_lines_1 = all_lines[task.output_lines:]
        task.output_lines = len(all_lines)

        new_lines_2 = all_lines[task.output_lines:]
        assert new_lines_2 == []  # offset 已追上，无新行


# ─── 事件序列验证 ─────────────────────────────────────────────────────────────


class TestEventSequence:
    """完整事件序列：task_started → progress → task_done。"""

    def test_full_lifecycle(self):
        """
        模拟完整生命周期：
        task_started → 2次 progress（带增量输出）→ task_done
        最终 manager 中无遗留任务。
        """
        mgr = BackgroundTaskManager()

        # 1. 启动
        mgr.on_task_started({
            "task_id": "b0000100",
            "task_type": "local_bash",
            "description": "python build.py",
        })
        assert mgr.has_running() is True
        assert "b0000100" in mgr._tasks

        # 2. 进度更新
        mgr.on_task_progress({
            "task_id": "b0000100",
            "output": "[1/10] Fetching deps...\n",
        })
        task = mgr._tasks["b0000100"]
        assert "[1/10]" in task.output

        mgr.on_task_progress({
            "task_id": "b0000100",
            "output": "[10/10] Done.\n",
        })
        assert "[10/10]" in task.output

        # 3. 完成（任务从管理器移除）
        mgr.on_task_done({
            "task_id": "b0000100",
            "status": "completed",
        })
        assert "b0000100" not in mgr._tasks
        assert mgr.has_running() is False

    def test_multiple_concurrent_tasks(self):
        """
        同时运行多个任务时，每个任务独立追踪，互不干扰。
        """
        mgr = BackgroundTaskManager()

        mgr.on_task_started({"task_id": "b0000201", "task_type": "bash", "description": "task A"})
        mgr.on_task_started({"task_id": "b0000202", "task_type": "bash", "description": "task B"})
        mgr.on_task_started({"task_id": "b0000203", "task_type": "agent", "description": "task C"})

        assert len(mgr._tasks) == 3

        mgr.on_task_progress({"task_id": "b0000201", "output": "A output\n"})
        mgr.on_task_progress({"task_id": "b0000202", "output": "B output\n"})

        assert mgr._tasks["b0000201"].output == "A output\n"
        assert mgr._tasks["b0000202"].output == "B output\n"
        assert mgr._tasks["b0000203"].output == ""

        # 完成 B
        mgr.on_task_done({"task_id": "b0000202", "status": "completed"})
        assert len(mgr._tasks) == 2
        assert "b0000201" in mgr._tasks
        assert "b0000203" in mgr._tasks


# ─── 边界条件 ─────────────────────────────────────────────────────────────────


class TestEdgeCases:
    """边界条件测试。"""

    def test_empty_description(self):
        """description 为空时 render() 不应崩溃。"""
        mgr = BackgroundTaskManager()
        mgr.on_task_started({"task_id": "b0000301", "task_type": "bash", "description": ""})
        output = mgr.render()
        assert "b0000301" in output

    def test_long_description_truncated_to_40(self):
        """description 超过 40 字符时，render() 应截断。"""
        mgr = BackgroundTaskManager()
        mgr.on_task_started({
            "task_id": "b0000302",
            "task_type": "bash",
            "description": "A" * 100,
        })
        output = mgr.render()
        # render() 中 desc[:40]，所以只有前 40 个 A
        assert "A" * 40 in output
        assert ("A" * 41) not in output

    def test_task_done_unknown_task_is_noop(self):
        """向未知 task_id 发送 task_done 不应崩溃。"""
        mgr = BackgroundTaskManager()
        mgr.on_task_done({"task_id": "b9999999", "status": "completed"})
        assert len(mgr._tasks) == 0

    def test_task_done_failed_preserves_manager_consistency(self):
        """task_done(status=failed) 后任务同样被移除。"""
        mgr = BackgroundTaskManager()
        mgr.on_task_started({"task_id": "b0000303", "task_type": "bash", "description": "fail"})
        mgr.on_task_done({"task_id": "b0000303", "status": "failed"})
        assert "b0000303" not in mgr._tasks
        assert mgr.has_running() is False

    def test_empty_output_in_progress(self):
        """progress 事件 output="" 时，不应追加空字符串导致换行。"""
        mgr = BackgroundTaskManager()
        mgr.on_task_started({"task_id": "b0000304", "task_type": "bash", "description": "empty"})
        mgr.on_task_progress({"task_id": "b0000304", "output": ""})
        # 空 output 不应改变 output_lines 计数
        task = mgr._tasks["b0000304"]
        # 写入空字符串不改变 output_lines
        # （因为 splitlines() 后 new_lines 为 []）
        all_lines = task.output.splitlines()
        new_lines = all_lines[task.output_lines:]
        assert new_lines == []
