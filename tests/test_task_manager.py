"""
tests/test_task_manager.py — TaskManager 单元测试

覆盖：
  - Task 创建与字段初始化
  - Task.to_dict() / Task.from_dict() 序列化与反序列化
  - Task.render_line() 状态标记
  - TaskManager.create() 正常/异常路径
  - TaskManager.get() 成功/不存在
  - TaskManager.update() 各字段更新/非法状态
  - TaskManager.bind_agent / complete / fail / can_start
  - TaskManager.list_all() 排除已删除任务
  - TaskManager.render_list() 格式输出
  - 自增 ID 行为
  - 持久化到 StorageAdapter（Mock）
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from ccserver.managers.tasks import Task, TaskManager


# ─── 辅助 ─────────────────────────────────────────────────────────────────────


def _make_tm(adapter=None):
    return TaskManager(session_id="test-session", adapter=adapter)


# ─── Task 类 ─────────────────────────────────────────────────────────────────


def test_task_initial_status():
    t = Task(task_id="1", subject="做某事", description="详情")
    assert t.status == "pending"


def test_task_to_dict():
    t = Task(
        task_id="42",
        subject="测试任务",
        description="描述内容",
        task_type="dev",
        agent_id="agent-1",
        agent_type="local_agent",
        blocked_by=["1", "2"],
        blocks=["3"],
    )
    d = t.to_dict()
    assert d == {
        "id": "42",
        "subject": "测试任务",
        "description": "描述内容",
        "status": "pending",
        "task_type": "dev",
        "agent_id": "agent-1",
        "agent_type": "local_agent",
        "blocked_by": ["1", "2"],
        "blocks": ["3"],
        "started_at": None,
        "completed_at": None,
        "output_summary": None,
        "output_data": None,
    }


def test_task_from_dict():
    data = {
        "id": "7",
        "subject": "反序列化测试",
        "description": "desc",
        "status": "in_progress",
        "task_type": "review",
        "agent_id": "agent-2",
        "agent_type": "background_agent",
        "blocked_by": ["3"],
        "blocks": ["4", "5"],
        "started_at": "2026-04-11T10:00:00+00:00",
        "completed_at": "2026-04-11T12:00:00+00:00",
        "output_summary": "done",
        "output_data": {"key": "value"},
    }
    t = Task.from_dict(data)
    assert t.id == "7"
    assert t.subject == "反序列化测试"
    assert t.status == "in_progress"
    assert t.task_type == "review"
    assert t.agent_id == "agent-2"
    assert t.agent_type == "background_agent"
    assert t.blocked_by == ["3"]
    assert t.blocks == ["4", "5"]
    assert t.started_at == datetime(2026, 4, 11, 10, 0, 0, tzinfo=timezone.utc)
    assert t.completed_at == datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
    assert t.output_summary == "done"
    assert t.output_data == {"key": "value"}


def test_task_render_line_pending():
    t = Task(task_id="1", subject="待处理", description="")
    assert t.render_line() == "[ ] #1: 待处理"


def test_task_render_line_in_progress():
    t = Task(task_id="2", subject="进行中", description="")
    t.status = "in_progress"
    assert t.render_line() == "[>] #2: 进行中"


def test_task_render_line_completed():
    t = Task(task_id="3", subject="已完成", description="")
    t.status = "completed"
    assert t.render_line() == "[x] #3: 已完成"


def test_task_render_line_failed():
    t = Task(task_id="4", subject="失败", description="")
    t.status = "failed"
    assert t.render_line() == "[!] #4: 失败"


def test_task_render_line_deleted():
    t = Task(task_id="5", subject="已删除", description="")
    t.status = "deleted"
    assert t.render_line() == "[d] #5: 已删除"


def test_task_render_line_with_agent():
    t = Task(task_id="6", subject="绑定Agent", description="", agent_id="neo")
    assert t.render_line() == "[ ] #6: 绑定Agent @neo"


# ─── TaskManager.create() ────────────────────────────────────────────────────


def test_create_returns_task():
    tm = _make_tm()
    task = tm.create("任务标题", "任务描述")
    assert isinstance(task, Task)
    assert task.subject == "任务标题"
    assert task.description == "任务描述"
    assert task.status == "pending"


def test_create_auto_increments_id():
    tm = _make_tm()
    t1 = tm.create("任务1", "")
    t2 = tm.create("任务2", "")
    t3 = tm.create("任务3", "")
    assert t1.id == "1"
    assert t2.id == "2"
    assert t3.id == "3"


def test_create_empty_subject_raises():
    tm = _make_tm()
    with pytest.raises(ValueError):
        tm.create("", "描述")


def test_create_whitespace_only_subject_raises():
    tm = _make_tm()
    with pytest.raises(ValueError):
        tm.create("   ", "描述")


def test_create_strips_subject():
    tm = _make_tm()
    t = tm.create("  任务  ", "描述")
    assert t.subject == "任务"


def test_create_strips_description():
    tm = _make_tm()
    t = tm.create("任务", "  描述内容  ")
    assert t.description == "描述内容"


def test_create_with_type_and_agent():
    tm = _make_tm()
    t = tm.create("任务", "desc", task_type="code", agent_id="a1", blocked_by=["1"], blocks=["2"])
    assert t.task_type == "code"
    assert t.agent_id == "a1"
    assert t.blocked_by == ["1"]
    assert t.blocks == ["2"]


# ─── TaskManager.get() ───────────────────────────────────────────────────────


def test_get_existing_task():
    tm = _make_tm()
    created = tm.create("测试", "")
    fetched = tm.get(created.id)
    assert fetched is created


def test_get_nonexistent_raises():
    tm = _make_tm()
    with pytest.raises(ValueError, match="not found"):
        tm.get("999")


def test_get_with_string_or_int_id():
    tm = _make_tm()
    t = tm.create("任务", "")
    # get() 内部会 str(task_id)，可以兼容整数类型传入
    fetched = tm.get(t.id)
    assert fetched.id == "1"


# ─── TaskManager.update() ────────────────────────────────────────────────────


def test_update_status():
    tm = _make_tm()
    t = tm.create("任务", "")
    updated = tm.update(t.id, status="in_progress")
    assert updated.status == "in_progress"
    assert tm.get(t.id).status == "in_progress"


def test_update_subject():
    tm = _make_tm()
    t = tm.create("旧标题", "")
    updated = tm.update(t.id, subject="新标题")
    assert updated.subject == "新标题"


def test_update_description():
    tm = _make_tm()
    t = tm.create("任务", "旧描述")
    updated = tm.update(t.id, description="新描述")
    assert updated.description == "新描述"


def test_update_multiple_fields():
    tm = _make_tm()
    t = tm.create("任务", "旧描述")
    updated = tm.update(
        t.id,
        status="completed",
        subject="新标题",
        description="新描述",
        task_type="test",
        agent_id="a9",
        agent_type="local_agent",
        blocked_by=["2"],
        blocks=["3"],
    )
    assert updated.status == "completed"
    assert updated.subject == "新标题"
    assert updated.description == "新描述"
    assert updated.task_type == "test"
    assert updated.agent_id == "a9"
    assert updated.agent_type == "local_agent"
    assert updated.blocked_by == ["2"]
    assert updated.blocks == ["3"]


def test_update_no_fields_keeps_original():
    tm = _make_tm()
    t = tm.create("任务", "描述")
    updated = tm.update(t.id)
    assert updated.status == "pending"
    assert updated.subject == "任务"
    assert updated.description == "描述"


def test_update_invalid_status_raises():
    tm = _make_tm()
    t = tm.create("任务", "")
    with pytest.raises(ValueError, match="Invalid status"):
        tm.update(t.id, status="unknown_status")


def test_update_empty_subject_raises():
    tm = _make_tm()
    t = tm.create("任务", "")
    with pytest.raises(ValueError, match="cannot be empty"):
        tm.update(t.id, subject="")


def test_update_all_valid_statuses():
    tm = _make_tm()
    for status in Task.VALID_STATUSES:
        t = tm.create(f"任务_{status}", "")
        updated = tm.update(t.id, status=status)
        assert updated.status == status


def test_update_nonexistent_task_raises():
    tm = _make_tm()
    with pytest.raises(ValueError):
        tm.update("999", status="completed")


# ─── TaskManager.bind_agent ──────────────────────────────────────────────────


def test_bind_agent():
    tm = _make_tm()
    t = tm.create("实现功能", "描述")
    updated = tm.bind_agent(t.id, agent_id="agent-007", agent_type="local_agent")
    assert updated.agent_id == "agent-007"
    assert updated.agent_type == "local_agent"
    assert updated.status == "in_progress"
    assert updated.started_at is not None


# ─── TaskManager.complete / fail ─────────────────────────────────────────────


def test_complete_task():
    tm = _make_tm()
    t = tm.create("任务", "")
    updated = tm.complete(t.id, summary="全部完成", output_data={"files": ["a.py"]})
    assert updated.status == "completed"
    assert updated.output_summary == "全部完成"
    assert updated.output_data == {"files": ["a.py"]}
    assert updated.completed_at is not None


def test_fail_task():
    tm = _make_tm()
    t = tm.create("任务", "")
    updated = tm.fail(t.id, reason="网络超时")
    assert updated.status == "failed"
    assert updated.output_summary == "网络超时"
    assert updated.completed_at is not None


# ─── TaskManager.can_start ───────────────────────────────────────────────────


def test_can_start_no_dependencies():
    tm = _make_tm()
    t = tm.create("独立任务", "")
    assert tm.can_start(t) is True


def test_can_start_dependency_completed():
    tm = _make_tm()
    dep = tm.create("前置任务", "")
    tm.complete(dep.id, "done")
    t = tm.create("后继任务", "", blocked_by=[dep.id])
    assert tm.can_start(t) is True


def test_can_start_dependency_pending():
    tm = _make_tm()
    dep = tm.create("前置任务", "")
    t = tm.create("后继任务", "", blocked_by=[dep.id])
    assert tm.can_start(t) is False


def test_can_start_dependency_failed():
    tm = _make_tm()
    dep = tm.create("前置任务", "")
    tm.fail(dep.id, "error")
    t = tm.create("后继任务", "", blocked_by=[dep.id])
    assert tm.can_start(t) is False


def test_can_start_missing_dependency():
    tm = _make_tm()
    t = tm.create("后继任务", "", blocked_by=["999"])
    assert tm.can_start(t) is False


def test_can_start_with_adapter():
    adapter = MagicMock()
    adapter.list_tasks.return_value = []
    adapter.get_task_counter.return_value = 0
    adapter.load_task.return_value = {
        "id": "1",
        "subject": "前置",
        "description": "",
        "status": "completed",
    }

    tm = _make_tm(adapter=adapter)
    t = Task(task_id="2", subject="后继", description="", blocked_by=["1"])
    tm._tasks["2"] = t
    assert tm.can_start(t) is True


# ─── TaskManager.list_all() ──────────────────────────────────────────────────


def test_list_all_empty():
    tm = _make_tm()
    assert tm.list_all() == []


def test_list_all_returns_non_deleted():
    tm = _make_tm()
    t1 = tm.create("任务1", "")
    t2 = tm.create("任务2", "")
    t3 = tm.create("任务3", "")
    tm.update(t2.id, status="deleted")

    result = tm.list_all()
    ids = [t.id for t in result]
    assert t1.id in ids
    assert t3.id in ids
    assert t2.id not in ids


def test_list_all_order_by_creation():
    tm = _make_tm()
    ids = [tm.create(f"任务{i}", "").id for i in range(5)]
    result_ids = [t.id for t in tm.list_all()]
    assert result_ids == ids


def test_list_all_excludes_only_deleted():
    tm = _make_tm()
    for i in range(3):
        t = tm.create(f"任务{i}", "")
        tm.update(t.id, status="deleted")
    assert tm.list_all() == []


# ─── TaskManager.render_list() ───────────────────────────────────────────────


def test_render_list_empty():
    tm = _make_tm()
    assert tm.render_list() == "No tasks."


def test_render_list_contains_tasks():
    tm = _make_tm()
    tm.create("第一个任务", "")
    tm.create("第二个任务", "")
    output = tm.render_list()
    assert "第一个任务" in output
    assert "第二个任务" in output


def test_render_list_shows_completion_count():
    tm = _make_tm()
    t1 = tm.create("任务1", "")
    t2 = tm.create("任务2", "")
    t3 = tm.create("任务3", "")
    tm.update(t1.id, status="completed")
    output = tm.render_list()
    assert "(1/3 completed)" in output


def test_render_list_excludes_deleted():
    tm = _make_tm()
    t1 = tm.create("保留", "")
    t2 = tm.create("删除", "")
    tm.update(t2.id, status="deleted")
    output = tm.render_list()
    assert "保留" in output
    assert "删除" not in output
    assert "(0/1 completed)" in output


def test_render_list_all_completed():
    tm = _make_tm()
    t1 = tm.create("任务1", "")
    t2 = tm.create("任务2", "")
    tm.update(t1.id, status="completed")
    tm.update(t2.id, status="completed")
    output = tm.render_list()
    assert "(2/2 completed)" in output


# ─── TaskManager persistence via mock adapter ────────────────────────────────


def test_persist_on_create():
    adapter = MagicMock()
    adapter.list_tasks.return_value = []
    adapter.get_task_counter.return_value = 0
    adapter.update_task = MagicMock()
    adapter.set_task_counter = MagicMock()

    tm = _make_tm(adapter=adapter)
    t = tm.create("持久化任务", "desc")

    assert t.id == "1"
    adapter.update_task.assert_called()
    adapter.set_task_counter.assert_called_with("test-session", 1)


def test_persist_on_update():
    adapter = MagicMock()
    adapter.list_tasks.return_value = []
    adapter.get_task_counter.return_value = 0

    tm = _make_tm(adapter=adapter)
    t = tm.create("任务", "desc")
    adapter.update_task.reset_mock()

    tm.update(t.id, status="completed")
    adapter.update_task.assert_called()


def test_load_from_storage():
    adapter = MagicMock()
    adapter.list_tasks.return_value = [
        {"id": "1", "subject": "已存任务", "description": "desc", "status": "in_progress"}
    ]
    adapter.get_task_counter.return_value = 5

    tm = _make_tm(adapter=adapter)
    tasks = tm.list_all()
    assert len(tasks) == 1
    assert tasks[0].id == "1"
    assert tasks[0].status == "in_progress"
    assert tm._counter == 5


def test_async_adapter_create():
    """模拟异步 adapter，验证 _maybe_await 能正确处理协程。"""

    async def async_update_task(sid, data):
        return None

    async def async_set_counter(sid, val):
        return None

    async def async_list_tasks(sid):
        return []

    async def async_get_counter(sid):
        return 0

    adapter = MagicMock()
    adapter.update_task.side_effect = async_update_task
    adapter.set_task_counter.side_effect = async_set_counter
    adapter.list_tasks.side_effect = async_list_tasks
    adapter.get_task_counter.side_effect = async_get_counter

    tm = _make_tm(adapter=adapter)
    t = tm.create("异步任务", "desc")
    assert t.id == "1"
