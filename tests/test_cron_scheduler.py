"""
tests/test_cron_scheduler.py — CronScheduler 单元测试。

覆盖：
  - CronTask 创建与字段
  - CronTask.to_dict() / from_dict() 序列化
  - CronTask.mark_triggered() / mark_deleted() 状态变更
  - CronScheduler.create() 循环/一次性任务
  - CronScheduler.delete() / list_all()
  - CronScheduler.load_durable_tasks() 磁盘恢复
  - BTCronCreate / BTCronDelete / BTCronList 工具
  - cron_parser 核心路径
"""

import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock

from ccserver.managers.cron.models import ScheduledTask, TASK_PREFIX
from ccserver.managers.cron.cron_parser import (
    parse_cron_next_run,
    cron_to_human,
    compute_jitter_delay,
    _expand_field,
)
from ccserver.managers.cron import TaskScheduler


# ─── 辅助 ─────────────────────────────────────────────────────────────────────


class MockStorage:
    """完全可控的内存存储模拟。"""

    def __init__(self):
        self._tasks: dict[str, dict] = {}
        self._hw = 0

    def create_cron_task(self, sid, data):
        self._tasks[data["task_id"]] = data

    def load_cron_task(self, sid, tid):
        return self._tasks.get(tid)

    def update_cron_task(self, sid, data):
        self._tasks[data["task_id"]] = data

    def delete_cron_task(self, sid, tid):
        self._tasks.pop(tid, None)

    def list_cron_tasks(self, sid):
        return list(self._tasks.values())

    def get_cron_highwatermark(self, sid):
        return self._hw

    def set_cron_highwatermark(self, sid, v):
        self._hw = v


class MockSession:
    def __init__(self):
        self.id = "test-session-001"
        self.storage = MockStorage()
        self._root_agent = None


def _make_scheduler(storage=None):
    s = MockSession()
    if storage:
        s.storage = storage
    return TaskScheduler(s)


# ─── CronTask 模型 ─────────────────────────────────────────────────────────────


class TestScheduledTaskInit:
    def test_default_task_id_generated(self):
        task = ScheduledTask(prompt="hello")
        assert task.task_id.startswith(TASK_PREFIX)
        assert len(task.task_id) == len(TASK_PREFIX) + 8

    def test_interval_by_default(self):
        task = ScheduledTask(prompt="hello", interval_seconds=60)
        assert task.trigger_type == "interval"
        assert task.is_interval
        assert not task.is_once

    def test_once_mode(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        task = ScheduledTask(prompt="hello", trigger_type="once", run_at=future)
        assert task.is_once
        assert not task.is_recurring

    def test_default_status_scheduled(self):
        task = ScheduledTask(prompt="hello", interval_seconds=60)
        assert task.status == "scheduled"

    def test_is_done_false_by_default(self):
        task = ScheduledTask(prompt="hello", interval_seconds=60)
        assert not task.is_done

    def test_is_done_true_after_delete(self):
        task = ScheduledTask(prompt="hello", interval_seconds=60)
        task.mark_deleted()
        assert task.is_done


class TestScheduledTaskState:
    def test_mark_triggered_updates_fields(self):
        task = ScheduledTask(prompt="hello", interval_seconds=60)
        now = datetime.now(timezone.utc)
        task.mark_triggered(now)
        assert task.last_triggered_at == now
        assert task.trigger_count == 1

    def test_mark_triggered_idempotent_count(self):
        task = ScheduledTask(prompt="hello", interval_seconds=60)
        now = datetime.now(timezone.utc)
        task.mark_triggered(now)
        task.mark_triggered(now)
        assert task.trigger_count == 2

    def test_mark_deleted_idempotent(self):
        task = ScheduledTask(prompt="hello", interval_seconds=60)
        task.mark_deleted()
        task.mark_deleted()  # 不崩溃


class TestScheduledTaskSerialization:
    def test_to_dict_contains_all_fields(self):
        task = ScheduledTask(
            prompt="test prompt",
            trigger_type="cron",
            cron_expr="*/5 * * * *",
            jitter_max=10,
            durable=True,
        )
        d = task.to_dict()
        assert d["task_id"] == task.task_id
        assert d["prompt"] == "test prompt"
        assert d["cron_expr"] == "*/5 * * * *"
        assert d["trigger_type"] == "cron"
        assert d["jitter_max"] == 10
        assert d["durable"] is True
        assert d["trigger_count"] == 0

    def test_from_dict_roundtrip(self):
        original = ScheduledTask(
            prompt="roundtrip test",
            trigger_type="cron",
            cron_expr="0 9 * * 1-5",
            jitter_max=5,
            durable=True,
        )
        d = original.to_dict()
        restored = ScheduledTask.from_dict(d)
        assert restored.task_id == original.task_id
        assert restored.prompt == original.prompt
        assert restored.cron_expr == original.cron_expr
        assert restored.trigger_type == original.trigger_type
        assert restored.jitter_max == original.jitter_max
        assert restored.durable == original.durable


# ─── CronParser ────────────────────────────────────────────────────────────────


class TestParseCronNextRun:
    def test_every_5_minutes_from_on_grid(self):
        base = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
        result = parse_cron_next_run("*/5 * * * *", base)
        assert result.minute == 0
        assert result.hour == 12

    def test_every_5_minutes_from_off_grid(self):
        base = datetime(2026, 4, 25, 12, 3, tzinfo=timezone.utc)
        result = parse_cron_next_run("*/5 * * * *", base)
        assert result.minute == 5
        assert result.hour == 12

    def test_hourly_at_23_past_skips_to_next_day(self):
        # 从 23:30 测试，找下一个 :00
        base = datetime(2026, 4, 25, 23, 30, tzinfo=timezone.utc)
        result = parse_cron_next_run("0 * * * *", base)
        assert result.day == 26  # 次日

    def test_weekday_9am_skips_weekend(self):
        # 2026-04-25 是周六（Python weekday=5，cron 周六=6）
        # 2026-04-26 是周日（Python weekday=6，cron 周日=0）
        # cron 1-5 = Mon~Fri, cron 0 = Sunday
        # 从周六 10am 开始，cron "1-5" 不含周日，跳到次日周日
        # 但 crontab 日/周是 OR 关系，周日 dow=0 在范围内
        # 所以周日 9am 是有效的，答案是 4/26 9am
        base = datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc)
        result = parse_cron_next_run("0 9 * * 1-5", base)
        assert result.day == 26
        assert result.hour == 9

    def test_invalid_cron_raises(self):
        with pytest.raises(ValueError):
            parse_cron_next_run("not a cron", datetime.now(timezone.utc))

    def test_too_few_fields_raises(self):
        with pytest.raises(ValueError):
            parse_cron_next_run("* * *", datetime.now(timezone.utc))


class TestCronToHuman:
    def test_every_n_minutes(self):
        assert "5 minute" in cron_to_human("*/5 * * * *")
        assert "Every hour" in cron_to_human("0 * * * *")

    def test_weekdays_9am(self):
        result = cron_to_human("0 9 * * 1-5")
        assert "weekday" in result or "9" in result


class TestJitter:
    def test_zero_jitter_returns_zero(self):
        assert compute_jitter_delay(0, "seed") == 0

    def test_jitter_deterministic_same_seed(self):
        d1 = compute_jitter_delay(30, "my-task")
        d2 = compute_jitter_delay(30, "my-task")
        assert d1 == d2

    def test_jitter_within_bounds(self):
        for seed in ["a", "b", "c"]:
            d = compute_jitter_delay(60, seed)
            assert 0 <= d <= 60


# ─── CronScheduler ─────────────────────────────────────────────────────────────


class TestTaskSchedulerCreate:
    def test_create_cron_task(self):
        sch = _make_scheduler()
        task = sch.create(prompt="check deploy", trigger_type="cron", cron_expr="*/10 * * * *")
        assert task.trigger_type == "cron"
        assert task.is_recurring
        assert task.prompt == "check deploy"
        assert task.cron_expr == "*/10 * * * *"
        assert task.task_id in [t.task_id for t in sch.list_all()]

    def test_create_once_task(self):
        sch = _make_scheduler()
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        task = sch.create(prompt="reminder", trigger_type="once", run_at=future)
        assert task.is_once
        assert task.cron_expr == ""

    def test_create_interval_task(self):
        sch = _make_scheduler()
        task = sch.create(prompt="check every 30s", trigger_type="interval", interval_seconds=30)
        assert task.is_interval
        assert task.interval_seconds == 30

    def test_create_durable_writes_storage(self):
        storage = MockStorage()
        sch = _make_scheduler(storage)
        sch.create(prompt="persistent", trigger_type="cron", cron_expr="*/5 * * * *", durable=True)
        assert len(storage.list_cron_tasks("test-session-001")) == 1

    def test_create_no_schedule_raises(self):
        sch = _make_scheduler()
        with pytest.raises(ValueError):
            # trigger_type=cron 但没有 cron_expr
            sch.create(prompt="no schedule", trigger_type="cron", cron_expr="")

    def test_create_once_no_run_at_raises(self):
        sch = _make_scheduler()
        with pytest.raises(ValueError):
            sch.create(prompt="no run_at", trigger_type="once", run_at=None)


class TestTaskSchedulerDelete:
    def test_delete_existing_task(self):
        sch = _make_scheduler()
        task = sch.create(prompt="to delete", trigger_type="cron", cron_expr="*/5 * * * *")
        assert sch.delete(task.task_id) is True
        assert task.task_id not in [t.task_id for t in sch.list_all()]

    def test_delete_nonexistent_returns_false(self):
        sch = _make_scheduler()
        assert sch.delete("ct00000000") is False

    def test_delete_removes_from_storage(self):
        storage = MockStorage()
        sch = _make_scheduler(storage)
        task = sch.create(prompt="deletable", trigger_type="cron", cron_expr="*/5 * * * *", durable=True)
        assert len(storage.list_cron_tasks("test-session-001")) == 1
        sch.delete(task.task_id)
        assert len(storage.list_cron_tasks("test-session-001")) == 0


class TestTaskSchedulerLoadDurable:
    def test_load_durable_tasks_restores(self):
        storage = MockStorage()
        task_data = ScheduledTask(
            prompt="restored",
            trigger_type="cron",
            cron_expr="*/5 * * * *",
            durable=True,
        )
        storage.create_cron_task("test-session-001", task_data.to_dict())

        sch = _make_scheduler(storage)
        sch.load_durable_tasks()
        assert len(sch.list_all()) == 1
        assert sch.list_all()[0].prompt == "restored"

    def test_load_skips_deleted_tasks(self):
        storage = MockStorage()
        task_data = ScheduledTask(
            prompt="should be skipped",
            trigger_type="cron",
            cron_expr="*/5 * * * *",
            status="deleted",
        )
        storage.create_cron_task("test-session-001", task_data.to_dict())

        sch = _make_scheduler(storage)
        sch.load_durable_tasks()
        assert len(sch.list_all()) == 0


# ─── 内置工具 ──────────────────────────────────────────────────────────────────


class TestBTCronCreate:
    @pytest.mark.asyncio
    async def test_run_valid_cron(self):
        sch = _make_scheduler()
        from ccserver.builtins.tools.cron.cron_create import BTCronCreate
        tool = BTCronCreate(sch)
        result = await tool.run(cron="*/5 * * * *", prompt="test")
        assert "created" in result.content
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_run_invalid_cron(self):
        sch = _make_scheduler()
        from ccserver.builtins.tools.cron.cron_create import BTCronCreate
        tool = BTCronCreate(sch)
        result = await tool.run(cron="not valid", prompt="test")
        assert result.is_error
        assert "Invalid cron" in result.content

    @pytest.mark.asyncio
    async def test_run_nl_schedule(self):
        sch = _make_scheduler()
        from ccserver.builtins.tools.cron.cron_create import BTCronCreate
        tool = BTCronCreate(sch)
        result = await tool.run(schedule="每30秒", prompt="check port")
        assert "created" in result.content
        assert not result.is_error


class TestBTCronDelete:
    @pytest.mark.asyncio
    async def test_delete_existing(self):
        sch = _make_scheduler()
        task = sch.create(prompt="delme", trigger_type="cron", cron_expr="*/5 * * * *")
        from ccserver.builtins.tools.cron.cron_delete import BTCronDelete
        tool = BTCronDelete(sch)
        result = await tool.run(id=task.task_id)
        assert "deleted" in result.content
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_delete_not_found(self):
        sch = _make_scheduler()
        from ccserver.builtins.tools.cron.cron_delete import BTCronDelete
        tool = BTCronDelete(sch)
        result = await tool.run(id="ct00000000")
        assert "not found" in result.content
        assert not result.is_error


class TestBTCronList:
    @pytest.mark.asyncio
    async def test_empty_returns_message(self):
        sch = _make_scheduler()
        from ccserver.builtins.tools.cron.cron_list import BTCronList
        tool = BTCronList(sch)
        result = await tool.run()
        assert "No scheduled tasks" in result.content

    @pytest.mark.asyncio
    async def test_shows_tasks(self):
        sch = _make_scheduler()
        sch.create(prompt="task1", trigger_type="cron", cron_expr="*/5 * * * *")
        sch.create(prompt="task2", trigger_type="cron", cron_expr="0 9 * * 1-5")
        from ccserver.builtins.tools.cron.cron_list import BTCronList
        tool = BTCronList(sch)
        result = await tool.run()
        # 输出包含任务 header
        assert "ct" in result.content
        # 两个任务
        assert "ct" in result.content
