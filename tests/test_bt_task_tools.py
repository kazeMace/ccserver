"""
tests/test_bt_task_tools.py — BTTaskCreate / BTTaskUpdate / BTTaskGet / BTTaskList 单元测试

覆盖：
  - BTTaskCreate: 正常创建、缺少必填参数返回错误
  - BTTaskUpdate: 更新状态/subject/description、task_id 不存在返回错误
  - BTTaskGet: 获取存在的任务、不存在返回错误
  - BTTaskList: 空列表返回字符串、有任务时输出包含 subject
  - 所有工具都通过 await tool(**kwargs) 调用（走 validate + run 链）
"""

import asyncio
import pytest

from ccserver.builtins.tools import BTTaskCreate
from ccserver.builtins.tools import BTTaskUpdate
from ccserver.builtins.tools import BTTaskGet
from ccserver.builtins.tools import BTTaskList
from ccserver.managers.tasks import TaskManager


# ─── 辅助 ─────────────────────────────────────────────────────────────────────


def _make_tools():
    mgr = TaskManager(session_id="test-session")
    return (
        BTTaskCreate(mgr),
        BTTaskUpdate(mgr),
        BTTaskGet(mgr),
        BTTaskList(mgr),
        mgr,
    )


# ─── BTTaskCreate ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_create_success():
    create, *_ = _make_tools()
    result = await create(subject="Fix bug", description="Details here")
    assert result.is_error is False
    assert "Fix bug" in result.content
    assert "Task #" in result.content


@pytest.mark.asyncio
async def test_task_create_missing_subject_returns_error():
    create, *_ = _make_tools()
    result = await create(description="Only desc, no subject")
    assert result.is_error is True
    assert "subject" in result.content


@pytest.mark.asyncio
async def test_task_create_missing_description_returns_error():
    create, *_ = _make_tools()
    result = await create(subject="Has subject only")
    assert result.is_error is True
    assert "description" in result.content


@pytest.mark.asyncio
async def test_task_create_multiple_increments_id():
    create, _, _, _, mgr = _make_tools()
    await create(subject="Task A", description="desc A")
    await create(subject="Task B", description="desc B")
    tasks = list(mgr._tasks.values())
    ids = {t.id for t in tasks}
    assert len(ids) == 2


# ─── BTTaskUpdate ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_update_status():
    create, update, _, _, mgr = _make_tools()
    await create(subject="Update me", description="desc")
    task_id = max(mgr._tasks.keys(), key=int)  # 刚创建的 id
    result = await update(task_id=str(task_id), status="in_progress")
    assert result.is_error is False
    assert "in_progress" in result.content


@pytest.mark.asyncio
async def test_task_update_subject():
    create, update, _, _, mgr = _make_tools()
    await create(subject="Old subject", description="desc")
    task_id = max(mgr._tasks.keys(), key=int)
    result = await update(task_id=str(task_id), subject="New subject")
    assert result.is_error is False
    assert "New subject" in result.content


@pytest.mark.asyncio
async def test_task_update_nonexistent_returns_error():
    _, update, *_ = _make_tools()
    result = await update(task_id="9999", status="completed")
    assert result.is_error is True


@pytest.mark.asyncio
async def test_task_update_missing_task_id_returns_error():
    _, update, *_ = _make_tools()
    result = await update(status="completed")  # 缺少 task_id
    assert result.is_error is True
    assert "task_id" in result.content


@pytest.mark.asyncio
async def test_task_update_deleted_status_removes_task():
    create, update, _, list_tool, mgr = _make_tools()
    await create(subject="To delete", description="desc")
    task_id = max(mgr._tasks.keys(), key=int)
    await update(task_id=str(task_id), status="deleted")
    list_result = await list_tool()
    assert "To delete" not in list_result.content


# ─── BTTaskGet ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_get_existing():
    create, _, get, _, mgr = _make_tools()
    await create(subject="Get me", description="My description")
    task_id = max(mgr._tasks.keys(), key=int)
    result = await get(task_id=str(task_id))
    assert result.is_error is False
    assert "Get me" in result.content
    assert "My description" in result.content


@pytest.mark.asyncio
async def test_task_get_nonexistent_returns_error():
    _, _, get, *_ = _make_tools()
    result = await get(task_id="9999")
    assert result.is_error is True


@pytest.mark.asyncio
async def test_task_get_missing_task_id_returns_error():
    _, _, get, *_ = _make_tools()
    result = await get()  # 缺少 task_id
    assert result.is_error is True
    assert "task_id" in result.content


# ─── BTTaskList ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_task_list_empty():
    _, _, _, list_tool, _ = _make_tools()
    result = await list_tool()
    assert result.is_error is False
    # 空列表时返回一个字符串（不报错）
    assert isinstance(result.content, str)


@pytest.mark.asyncio
async def test_task_list_shows_created_tasks():
    create, _, _, list_tool, _ = _make_tools()
    await create(subject="Task Alpha", description="desc")
    await create(subject="Task Beta", description="desc")
    result = await list_tool()
    assert result.is_error is False
    assert "Task Alpha" in result.content
    assert "Task Beta" in result.content


@pytest.mark.asyncio
async def test_task_list_no_params_required():
    """BTTaskList.params 为空，调用时不需要任何参数。"""
    _, _, _, list_tool, _ = _make_tools()
    result = await list_tool()
    assert result.is_error is False


# ─── schema 验证 ──────────────────────────────────────────────────────────────


def test_task_create_schema():
    create, *_ = _make_tools()
    schema = create.to_schema()
    assert schema["name"] == "TaskCreate"
    assert "subject" in schema["input_schema"]["required"]
    assert "description" in schema["input_schema"]["required"]


def test_task_update_schema():
    _, update, *_ = _make_tools()
    schema = update.to_schema()
    assert schema["name"] == "TaskUpdate"
    assert "task_id" in schema["input_schema"]["required"]
    # status, subject, description 是可选的
    assert "status" not in schema["input_schema"].get("required", [])


def test_task_get_schema():
    _, _, get, *_ = _make_tools()
    schema = get.to_schema()
    assert schema["name"] == "TaskGet"
    assert "task_id" in schema["input_schema"]["required"]


def test_task_list_schema():
    _, _, _, list_tool, _ = _make_tools()
    schema = list_tool.to_schema()
    assert schema["name"] == "TaskList"
    # 无必填参数
    assert "required" not in schema["input_schema"]
