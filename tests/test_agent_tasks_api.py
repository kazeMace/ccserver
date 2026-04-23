"""
tests/test_agent_tasks_api.py — Agent Task HTTP API 端点测试。

覆盖：
  - GET  /sessions/{id}/agent-tasks          — 列出所有 Agent 后台任务
  - GET  /sessions/{id}/agent-tasks/{id}     — 查询单个 Agent 任务
  - POST /sessions/{id}/agent-tasks/{id}/cancel — 取消运行中的 Agent 任务
  - 404 / 409 错误处理
"""

import pytest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from ccserver.session import Session
from ccserver.tasks import AgentTaskState, AgentTaskStatus, generate_agent_id


# ─── Test fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def unique_sessions():
    """
    为每个测试创建完全独立的 session，session_id 使用 UUID 避免冲突。
    每次调用返回一个新的 session 实例。
    同时清理全局 agent_registry。
    """
    from ccserver.agent_registry import _HANDLE_REGISTRY
    _HANDLE_REGISTRY.clear()
    session = Session(
        id=f"test-agent-{uuid.uuid4().hex[:8]}",
        workdir=Path("/tmp"),
        project_root=Path("/tmp"),
    )
    yield session
    # teardown：清理全局句柄表
    _HANDLE_REGISTRY.clear()


@pytest.fixture
def client():
    """创建 FastAPI TestClient，使用真实 app。"""
    from server import app
    return TestClient(app)


# ─── GET /sessions/{id}/agent-tasks ──────────────────────────────────────────


def test_list_agent_tasks_empty(unique_sessions, client, monkeypatch):
    """空 session 应返回空列表。"""
    from server import session_manager
    monkeypatch.setattr(session_manager, "_sessions", {unique_sessions.id: unique_sessions})

    response = client.get(f"/sessions/{unique_sessions.id}/agent-tasks")
    assert response.status_code == 200
    data = response.json()
    assert data["tasks"] == []
    assert data["summary"]["total"] == 0


def test_list_agent_tasks_with_running_task(unique_sessions, client, monkeypatch):
    """有 running 任务时应正确返回计数。"""
    task = AgentTaskState(
        id=generate_agent_id(),
        agent_id="agent-001",
        agent_name="coder",
        prompt="write code",
    )
    task.mark_running()
    unique_sessions.agent_tasks.register(task)

    from server import session_manager
    monkeypatch.setattr(session_manager, "_sessions", {unique_sessions.id: unique_sessions})

    response = client.get(f"/sessions/{unique_sessions.id}/agent-tasks")
    assert response.status_code == 200
    data = response.json()
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["status"] == AgentTaskStatus.RUNNING
    assert data["tasks"][0]["agent_name"] == "coder"
    assert data["summary"]["running"] == 1


def test_list_agent_tasks_nonexistent_session(client):
    """不存在的 session 应返回 404。"""
    response = client.get(f"/sessions/nonexistent-session-{uuid.uuid4().hex[:8]}/agent-tasks")
    assert response.status_code == 404


# ─── GET /sessions/{id}/agent-tasks/{task_id} ──────────────────────────────────


def test_get_agent_task_success(unique_sessions, client, monkeypatch):
    """存在的任务应返回完整 to_dict。"""
    task = AgentTaskState(
        id=generate_agent_id(),
        agent_id="agent-002",
        agent_name="researcher",
        description="Research topic",
        prompt="research AI",
    )
    task.mark_running()
    task.mark_completed(result="found results")
    unique_sessions.agent_tasks.register(task)
    task_id = task.id

    from server import session_manager
    monkeypatch.setattr(session_manager, "_sessions", {unique_sessions.id: unique_sessions})

    response = client.get(f"/sessions/{unique_sessions.id}/agent-tasks/{task_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == task_id
    assert data["agent_id"] == "agent-002"
    assert data["agent_name"] == "researcher"
    assert data["status"] == AgentTaskStatus.COMPLETED
    assert data["result"] == "found results"


def test_get_agent_task_not_found(unique_sessions, client, monkeypatch):
    """不存在的 task_id 应返回 404。"""
    from server import session_manager
    monkeypatch.setattr(session_manager, "_sessions", {unique_sessions.id: unique_sessions})

    response = client.get(f"/sessions/{unique_sessions.id}/agent-tasks/anotexist")
    assert response.status_code == 404


# ─── POST /sessions/{id}/agent-tasks/{task_id}/cancel ─────────────────────────


def test_cancel_running_task_with_handle(unique_sessions, client, monkeypatch):
    """cancel 运行中的任务（有 handle 时）应返回 200。"""
    task = AgentTaskState(
        id=generate_agent_id(),
        agent_id="agent-003",
        agent_name="builder",
        prompt="build project",
    )
    task.mark_running()
    unique_sessions.agent_tasks.register(task)

    # mock handle，带有 cancel 方法（async def 才能被 asyncio.create_task 正确调度）
    cancel_called = False

    class MockHandle:
        agent_id = "agent-003"

        async def cancel(self):
            nonlocal cancel_called
            cancel_called = True

    from ccserver.agent_registry import _HANDLE_REGISTRY
    mock_handle = MockHandle()
    _HANDLE_REGISTRY["agent-003"] = mock_handle

    from server import session_manager
    monkeypatch.setattr(session_manager, "_sessions", {unique_sessions.id: unique_sessions})

    try:
        response = client.post(
            f"/sessions/{unique_sessions.id}/agent-tasks/{task.id}/cancel"
        )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        # 注意：cancel() 为 async，通过 asyncio.create_task 调度，
        # 同步 TestClient 不保证其完成，此处仅验证调度成功
    finally:
        _HANDLE_REGISTRY.pop("agent-003", None)


def test_cancel_nonexistent_task(unique_sessions, client, monkeypatch):
    """cancel 不存在的任务应返回 404。"""
    from server import session_manager
    monkeypatch.setattr(session_manager, "_sessions", {unique_sessions.id: unique_sessions})

    response = client.post(
        f"/sessions/{unique_sessions.id}/agent-tasks/anotexist/cancel"
    )
    assert response.status_code == 404


def test_cancel_already_completed_task(unique_sessions, client, monkeypatch):
    """cancel 已完成的任务应返回 409。"""
    task = AgentTaskState(
        id=generate_agent_id(),
        agent_id="agent-004",
        prompt="hello",
    )
    task.mark_running()
    task.mark_completed(result="done")
    unique_sessions.agent_tasks.register(task)

    from server import session_manager
    monkeypatch.setattr(session_manager, "_sessions", {unique_sessions.id: unique_sessions})

    response = client.post(
        f"/sessions/{unique_sessions.id}/agent-tasks/{task.id}/cancel"
    )
    assert response.status_code == 409
    assert "not running" in response.json()["detail"]


def test_cancel_already_cancelled_task(unique_sessions, client, monkeypatch):
    """cancel 已取消的任务应返回 409。"""
    task = AgentTaskState(
        id=generate_agent_id(),
        agent_id="agent-005",
        prompt="hello",
    )
    task.mark_running()
    task.mark_cancelled()
    unique_sessions.agent_tasks.register(task)

    from server import session_manager
    monkeypatch.setattr(session_manager, "_sessions", {unique_sessions.id: unique_sessions})

    response = client.post(
        f"/sessions/{unique_sessions.id}/agent-tasks/{task.id}/cancel"
    )
    assert response.status_code == 409
    assert "not running" in response.json()["detail"]
