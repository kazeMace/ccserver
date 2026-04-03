"""
tests/test_task_manager.py — TaskManager 单元测试

覆盖：
  - Task 创建与字段初始化
  - Task.render_line() 状态标记
  - Task.to_dict() 序列化
  - TaskManager.create() 正常/异常路径
  - TaskManager.get() 成功/不存在
  - TaskManager.update() 各字段更新/非法状态
  - TaskManager.list_all() 排除已删除任务
  - TaskManager.render_list() 格式输出
  - 自增 ID 行为
"""

import pytest

from ccserver.task_manager import Task, TaskManager


# ─── Task 类 ─────────────────────────────────────────────────────────────────


def test_task_initial_status():
    t = Task(task_id="1", subject="做某事", description="详情")
    assert t.status == "pending"


def test_task_to_dict():
    t = Task(task_id="42", subject="测试任务", description="描述内容")
    d = t.to_dict()
    assert d == {"id": "42", "subject": "测试任务", "description": "描述内容", "status": "pending"}


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


def test_task_render_line_deleted():
    t = Task(task_id="4", subject="已删除", description="")
    t.status = "deleted"
    assert t.render_line() == "[d] #4: 已删除"


# ─── TaskManager.create() ────────────────────────────────────────────────────


def test_create_returns_task():
    tm = TaskManager()
    task = tm.create("任务标题", "任务描述")
    assert isinstance(task, Task)
    assert task.subject == "任务标题"
    assert task.description == "任务描述"
    assert task.status == "pending"


def test_create_auto_increments_id():
    tm = TaskManager()
    t1 = tm.create("任务1", "")
    t2 = tm.create("任务2", "")
    t3 = tm.create("任务3", "")
    assert t1.id == "1"
    assert t2.id == "2"
    assert t3.id == "3"


def test_create_empty_subject_raises():
    tm = TaskManager()
    with pytest.raises(ValueError):
        tm.create("", "描述")


def test_create_whitespace_only_subject_raises():
    tm = TaskManager()
    with pytest.raises(ValueError):
        tm.create("   ", "描述")


def test_create_strips_subject():
    tm = TaskManager()
    t = tm.create("  任务  ", "描述")
    assert t.subject == "任务"


def test_create_strips_description():
    tm = TaskManager()
    t = tm.create("任务", "  描述内容  ")
    assert t.description == "描述内容"


# ─── TaskManager.get() ───────────────────────────────────────────────────────


def test_get_existing_task():
    tm = TaskManager()
    created = tm.create("测试", "")
    fetched = tm.get(created.id)
    assert fetched is created


def test_get_nonexistent_raises():
    tm = TaskManager()
    with pytest.raises(ValueError, match="not found"):
        tm.get("999")


def test_get_with_string_or_int_id():
    tm = TaskManager()
    t = tm.create("任务", "")
    # get() 内部会 str(task_id)，可以兼容整数类型传入
    fetched = tm.get(t.id)
    assert fetched.id == "1"


# ─── TaskManager.update() ────────────────────────────────────────────────────


def test_update_status():
    tm = TaskManager()
    t = tm.create("任务", "")
    updated = tm.update(t.id, status="in_progress")
    assert updated.status == "in_progress"
    assert tm.get(t.id).status == "in_progress"


def test_update_subject():
    tm = TaskManager()
    t = tm.create("旧标题", "")
    updated = tm.update(t.id, subject="新标题")
    assert updated.subject == "新标题"


def test_update_description():
    tm = TaskManager()
    t = tm.create("任务", "旧描述")
    updated = tm.update(t.id, description="新描述")
    assert updated.description == "新描述"


def test_update_multiple_fields():
    tm = TaskManager()
    t = tm.create("任务", "旧描述")
    updated = tm.update(t.id, status="completed", subject="新标题", description="新描述")
    assert updated.status == "completed"
    assert updated.subject == "新标题"
    assert updated.description == "新描述"


def test_update_no_fields_keeps_original():
    tm = TaskManager()
    t = tm.create("任务", "描述")
    updated = tm.update(t.id)
    assert updated.status == "pending"
    assert updated.subject == "任务"
    assert updated.description == "描述"


def test_update_invalid_status_raises():
    tm = TaskManager()
    t = tm.create("任务", "")
    with pytest.raises(ValueError, match="Invalid status"):
        tm.update(t.id, status="unknown_status")


def test_update_empty_subject_raises():
    tm = TaskManager()
    t = tm.create("任务", "")
    with pytest.raises(ValueError, match="cannot be empty"):
        tm.update(t.id, subject="")


def test_update_all_valid_statuses():
    tm = TaskManager()
    for status in Task.VALID_STATUSES:
        t = tm.create(f"任务_{status}", "")
        updated = tm.update(t.id, status=status)
        assert updated.status == status


def test_update_nonexistent_task_raises():
    tm = TaskManager()
    with pytest.raises(ValueError):
        tm.update("999", status="completed")


# ─── TaskManager.list_all() ──────────────────────────────────────────────────


def test_list_all_empty():
    tm = TaskManager()
    assert tm.list_all() == []


def test_list_all_returns_non_deleted():
    tm = TaskManager()
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
    tm = TaskManager()
    ids = [tm.create(f"任务{i}", "").id for i in range(5)]
    result_ids = [t.id for t in tm.list_all()]
    assert result_ids == ids


def test_list_all_excludes_only_deleted():
    tm = TaskManager()
    for i in range(3):
        t = tm.create(f"任务{i}", "")
        tm.update(t.id, status="deleted")
    assert tm.list_all() == []


# ─── TaskManager.render_list() ───────────────────────────────────────────────


def test_render_list_empty():
    tm = TaskManager()
    assert tm.render_list() == "No tasks."


def test_render_list_contains_tasks():
    tm = TaskManager()
    tm.create("第一个任务", "")
    tm.create("第二个任务", "")
    output = tm.render_list()
    assert "第一个任务" in output
    assert "第二个任务" in output


def test_render_list_shows_completion_count():
    tm = TaskManager()
    t1 = tm.create("任务1", "")
    t2 = tm.create("任务2", "")
    t3 = tm.create("任务3", "")
    tm.update(t1.id, status="completed")
    output = tm.render_list()
    assert "(1/3 completed)" in output


def test_render_list_excludes_deleted():
    tm = TaskManager()
    t1 = tm.create("保留", "")
    t2 = tm.create("删除", "")
    tm.update(t2.id, status="deleted")
    output = tm.render_list()
    assert "保留" in output
    assert "删除" not in output
    assert "(0/1 completed)" in output


def test_render_list_all_completed():
    tm = TaskManager()
    t1 = tm.create("任务1", "")
    t2 = tm.create("任务2", "")
    tm.update(t1.id, status="completed")
    tm.update(t2.id, status="completed")
    output = tm.render_list()
    assert "(2/2 completed)" in output
