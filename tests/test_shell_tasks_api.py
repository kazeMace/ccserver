"""
tests/test_shell_tasks_api.py — Shell Task HTTP API 端点测试。

覆盖：
  - GET  /sessions/{id}/tasks          — 列出所有任务
  - GET  /sessions/{id}/tasks/{id}     — 查询单个任务
  - POST /sessions/{id}/tasks/{id}/kill — 终止运行中的任务
  - 404 / 409 错误处理
"""

import pytest
from fastapi.testclient import TestClient

from ccserver.session import Session
from ccserver.tasks import TaskStatus
from ccserver.builtins.tools.bash import BTBash


# ─── Test fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def session():
    """创建一个测试用的 Session。"""
    from pathlib import Path
    return Session(
        id="test-api-session",
        workdir=Path("/tmp"),
        project_root=Path("/tmp"),
    )


@pytest.fixture
def client():
    """创建 FastAPI TestClient，使用真实 app。"""
    # 延迟导入避免循环
    from server import app
    return TestClient(app)


# ─── GET /sessions/{id}/tasks ────────────────────────────────────────────────


def test_list_tasks_empty(session, client, monkeypatch):
    """空 session 应返回空列表。"""
    # 替换全局 session_manager 中的 session
    from server import session_manager
    monkeypatch.setattr(session_manager, "_sessions", {"test-api-session": session})

    response = client.get("/sessions/test-api-session/tasks")
    assert response.status_code == 200
    data = response.json()
    assert data["tasks"] == []
    assert data["summary"]["total"] == 0


def test_list_tasks_with_running_task(session, client, monkeypatch):
    """有 running 任务时应正确返回计数。"""
    from pathlib import Path
    from ccserver.tasks import ShellTaskState, generate_shell_id

    task = ShellTaskState(id=generate_shell_id(), command="sleep 60")
    task.mark_running(pid=12345, proc="mock_proc")
    session.shell_tasks.register(task)

    from server import session_manager
    monkeypatch.setattr(session_manager, "_sessions", {"test-api-session": session})

    response = client.get("/sessions/test-api-session/tasks")
    assert response.status_code == 200
    data = response.json()
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["status"] == TaskStatus.RUNNING
    assert data["summary"]["running"] == 1


def test_list_tasks_nonexistent_session(client):
    """不存在的 session 应返回 404。"""
    response = client.get("/sessions/nonexistent-session-id/tasks")
    assert response.status_code == 404


# ─── GET /sessions/{id}/tasks/{task_id} ──────────────────────────────────────


def test_get_task_success(session, client, monkeypatch):
    """存在的任务应返回完整 to_dict。"""
    from ccserver.tasks import ShellTaskState, generate_shell_id

    task = ShellTaskState(
        id=generate_shell_id(),
        command="echo hello",
        description="test echo",
    )
    task.mark_running(pid=999, proc="mock_proc")
    task.append_output("hello\n")
    task.mark_completed(exit_code=0)
    session.shell_tasks.register(task)
    task_id = task.id

    from server import session_manager
    monkeypatch.setattr(session_manager, "_sessions", {"test-api-session": session})

    response = client.get(f"/sessions/test-api-session/tasks/{task_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == task_id
    assert data["type"] == "local_bash"
    assert data["status"] == TaskStatus.COMPLETED
    assert data["exit_code"] == 0
    assert "hello" in data["output"]


def test_get_task_not_found(session, client, monkeypatch):
    """不存在的 task_id 应返回 404。"""
    from server import session_manager
    monkeypatch.setattr(session_manager, "_sessions", {"test-api-session": session})

    response = client.get("/sessions/test-api-session/tasks/bnotexist")
    assert response.status_code == 404


# ─── POST /sessions/{id}/tasks/{task_id}/kill ────────────────────────────────


def test_kill_running_task(session, client, monkeypatch):
    """kill 运行中的任务应返回 200 并改变状态。"""
    from ccserver.tasks import ShellTaskState, generate_shell_id

    task = ShellTaskState(id=generate_shell_id(), command="sleep 60")

    # 用 mock proc，避免真正 kill
    class MockProc:
        killed = False

        def kill(self):
            self.killed = True

    mock_proc = MockProc()
    task.mark_running(pid=999, proc=mock_proc)
    session.shell_tasks.register(task)
    task_id = task.id

    from server import session_manager
    monkeypatch.setattr(session_manager, "_sessions", {"test-api-session": session})

    response = client.post(f"/sessions/test-api-session/tasks/{task_id}/kill")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert task.status == TaskStatus.KILLED
    assert mock_proc.killed is True


def test_kill_nonexistent_task(session, client, monkeypatch):
    """kill 不存在的任务应返回 404。"""
    from server import session_manager
    monkeypatch.setattr(session_manager, "_sessions", {"test-api-session": session})

    response = client.post("/sessions/test-api-session/tasks/bnotexist/kill")
    assert response.status_code == 404


def test_kill_already_completed_task(session, client, monkeypatch):
    """kill 已完成的任务应返回 409。"""
    from ccserver.tasks import ShellTaskState, generate_shell_id

    task = ShellTaskState(id=generate_shell_id(), command="echo hi")
    task.mark_running(pid=1, proc="p")
    task.mark_completed(exit_code=0)
    session.shell_tasks.register(task)
    task_id = task.id

    from server import session_manager
    monkeypatch.setattr(session_manager, "_sessions", {"test-api-session": session})

    response = client.post(f"/sessions/test-api-session/tasks/{task_id}/kill")
    assert response.status_code == 409
    assert "not running" in response.json()["detail"]


def test_kill_already_killed_task(session, client, monkeypatch):
    """kill 已终止的任务应返回 409。"""
    from ccserver.tasks import ShellTaskState, generate_shell_id

    task = ShellTaskState(id=generate_shell_id(), command="sleep 60")

    class MockProc:
        killed = False

        def kill(self):
            self.killed = True

    task.mark_running(pid=1, proc=MockProc())
    task.mark_killed(reason="test")
    session.shell_tasks.register(task)
    task_id = task.id

    from server import session_manager
    monkeypatch.setattr(session_manager, "_sessions", {"test-api-session": session})

    response = client.post(f"/sessions/test-api-session/tasks/{task_id}/kill")
    assert response.status_code == 409
