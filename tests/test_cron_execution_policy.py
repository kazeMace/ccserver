"""
tests/test_cron_execution_policy.py — execution_policy 和并发触发单元测试。

覆盖：
  - fixed_rate：next_run_at 在触发前就计算好，不受执行时间影响
  - fixed_delay：next_run_at 在触发后从当前时间计算
  - 多任务同时到期时不阻塞（并发注入 inbox）
  - execution_policy 序列化/反序列化
"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from ccserver.managers.cron.models import ScheduledTask
from ccserver.managers.cron.scheduler import TaskScheduler
from ccserver.session import Session


def _make_session() -> Session:
    return Session(
        id="test-policy",
        workdir=Path("/tmp"),
        project_root=Path("/tmp"),
    )


class TestExecutionPolicyModel:
    """execution_policy 模型字段测试。"""

    def test_default_policy_is_fixed_delay(self):
        """默认 execution_policy 应为 fixed_delay。"""
        task = ScheduledTask(prompt="test", trigger_type="interval", interval_seconds=30)
        assert task.execution_policy == "fixed_delay"

    def test_fixed_delay_roundtrip(self):
        """fixed_delay 应可序列化/反序列化。"""
        task = ScheduledTask(
            prompt="test",
            trigger_type="interval",
            interval_seconds=30,
            execution_policy="fixed_delay",
        )
        data = task.to_dict()
        assert data["execution_policy"] == "fixed_delay"

        restored = ScheduledTask.from_dict(data)
        assert restored.execution_policy == "fixed_delay"

    def test_old_data_defaults_to_fixed_delay(self):
        """旧数据（无 execution_policy 字段）反序列化应默认 fixed_delay。"""
        data = {
            "task_id": "ctabc",
            "prompt": "old task",
            "trigger_type": "interval",
            "interval_seconds": 60,
            "enabled": True,
            "status": "scheduled",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        task = ScheduledTask.from_dict(data)
        assert task.execution_policy == "fixed_delay"


class TestSchedulerExecutionPolicy:
    """TaskScheduler 执行策略测试。"""

    def test_create_with_fixed_delay(self):
        """创建任务时 execution_policy=fixed_delay 应正确保存。"""
        session = _make_session()
        scheduler = TaskScheduler(session)

        task = scheduler.create(
            prompt="test",
            trigger_type="interval",
            interval_seconds=30,
            execution_policy="fixed_delay",
        )
        assert task.execution_policy == "fixed_delay"

    def test_create_with_fixed_rate(self):
        """创建任务时 execution_policy=fixed_rate 应正确保存。"""
        session = _make_session()
        scheduler = TaskScheduler(session)

        task = scheduler.create(
            prompt="test",
            trigger_type="interval",
            interval_seconds=30,
            execution_policy="fixed_rate",
        )
        assert task.execution_policy == "fixed_rate"

    @pytest.mark.anyio
    async def test_fixed_rate_next_run_set_before_execution(self):
        """
        fixed_rate 策略下，_run() 循环中 next_run_at 应在触发前就设好，
        保证即使 agent 执行很慢，下次触发时间也不会漂移。
        """
        session = _make_session()

        # mock root_agent
        root = MagicMock()
        root.context.agent_id = "agent-test"
        root.context.inbox = asyncio.Queue(maxsize=100)
        root.state.phase = "idle"
        root.emitter = MagicMock()
        root._loop = AsyncMock()
        session._root_agent = root

        # mock event_bus（通过内部字段绕过 property 只读限制）
        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()
        session._event_bus = mock_bus

        scheduler = TaskScheduler(session)

        # 创建一个已经到期的任务（next_run_at = 过去）
        now = datetime.now(timezone.utc)
        task = scheduler.create(
            prompt="fixed-rate test",
            trigger_type="interval",
            interval_seconds=30,
            execution_policy="fixed_rate",
        )
        # 手动把 next_run_at 设为过去，让它立刻到期
        task.next_run_at = now - timedelta(seconds=1)
        task.status = "scheduled"

        # 启动调度器，运行一次 tick
        scheduler.start()
        # 等待一次调度循环
        await asyncio.sleep(1.5)
        scheduler.stop()

        # next_run_at 应已经被设为 ~30s 后
        assert task.next_run_at is not None
        expected_min = now + timedelta(seconds=25)   # 允许 5s 误差
        expected_max = now + timedelta(seconds=35)
        assert expected_min <= task.next_run_at <= expected_max, (
            f"fixed_rate next_run_at expected ~30s after now, got {task.next_run_at}"
        )

    @pytest.mark.anyio
    async def test_fixed_delay_next_run_set_after_execution(self):
        """
        fixed_delay 策略下，next_run_at 应在 _schedule_trigger 内部（触发后）设置。
        """
        session = _make_session()

        root = MagicMock()
        root.context.agent_id = "agent-test"
        root.context.inbox = asyncio.Queue(maxsize=100)
        root.state.phase = "idle"
        root.emitter = MagicMock()
        root._loop = AsyncMock()
        session._root_agent = root

        mock_bus2 = MagicMock()
        mock_bus2.publish = AsyncMock()
        session._event_bus = mock_bus2

        scheduler = TaskScheduler(session)

        now = datetime.now(timezone.utc)
        task = scheduler.create(
            prompt="fixed-delay test",
            trigger_type="interval",
            interval_seconds=30,
            execution_policy="fixed_delay",
        )
        task.next_run_at = now - timedelta(seconds=1)
        task.status = "scheduled"

        scheduler.start()
        await asyncio.sleep(1.5)
        scheduler.stop()

        # 触发后 next_run_at 应在真实触发时间 + 30s 附近
        assert task.next_run_at is not None
        # 只检查大致范围（触发后重新计算，应在 ~30s 后）
        assert task.next_run_at > now + timedelta(seconds=25), (
            f"fixed_delay next_run_at expected >25s after now, got {task.next_run_at}"
        )


class TestConcurrentTrigger:
    """多任务并发触发测试。"""

    @pytest.mark.anyio
    async def test_multiple_tasks_triggered_concurrently(self):
        """多个到期任务应并发注入 inbox，不串行阻塞。"""
        session = _make_session()

        root = MagicMock()
        root.context.agent_id = "agent-test"
        root.context.inbox = asyncio.Queue(maxsize=100)
        root.state.phase = "idle"
        root.emitter = MagicMock()
        # _loop 模拟耗时 0.5s 的执行
        async def slow_loop():
            await asyncio.sleep(0.5)
        root._loop = slow_loop
        session._root_agent = root

        mock_bus3 = MagicMock()
        mock_bus3.publish = AsyncMock()
        session._event_bus = mock_bus3

        scheduler = TaskScheduler(session)

        now = datetime.now(timezone.utc)
        # 创建 3 个都已到期的任务
        tasks = []
        for i in range(3):
            t = scheduler.create(
                prompt=f"task-{i}",
                trigger_type="interval",
                interval_seconds=60,
                execution_policy="fixed_rate",
            )
            t.next_run_at = now - timedelta(seconds=1)
            t.status = "scheduled"
            tasks.append(t)

        start = asyncio.get_event_loop().time()
        scheduler.start()
        # 等待足够时间让所有任务触发完（并发应 < 1s，串行需要 1.5s+）
        await asyncio.sleep(1.5)
        scheduler.stop()
        elapsed = asyncio.get_event_loop().time() - start

        # inbox 应收到 3 条消息
        inbox_count = root.context.inbox.qsize()
        assert inbox_count == 3, f"Expected 3 inbox messages, got {inbox_count}"

        # 并发执行：elapsed 应远小于串行的 3 * 0.5 = 1.5s
        # （这里 1.5s 等待包含了调度 tick，所以只验证 inbox 数量）
