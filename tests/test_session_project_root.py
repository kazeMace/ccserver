# tests/test_session_project_root.py
import importlib
from pathlib import Path


def test_session_manager_uses_project_dir(tmp_path, monkeypatch):
    """不传 project_root 时，SessionManager 应使用 config.PROJECT_DIR。

    特意把 PROJECT_DIR 设置成与 base_dir.parent 不同的路径，
    以区分两种实现：base_dir.parent vs config.PROJECT_DIR。
    """
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    monkeypatch.setenv("CCSERVER_PROJECT_DIR", str(project_dir.resolve()))

    import ccserver.config as config
    importlib.reload(config)

    from ccserver.storage import FileStorageAdapter
    import ccserver.session as session_mod
    importlib.reload(session_mod)

    # sessions_dir 故意放在 tmp_path 下（而非 project_dir 下）
    # 使得 base_dir.parent == tmp_path != project_dir
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    storage = FileStorageAdapter(sessions_dir)
    sm = session_mod.SessionManager(
        base_dir=sessions_dir,
        storage=storage,
    )
    assert sm.project_root == project_dir.resolve()


def test_session_manager_explicit_project_root(tmp_path, monkeypatch):
    """显式传入 project_root 时，应优先使用传入值。"""
    other = tmp_path / "other"
    other.mkdir()

    from ccserver.storage import FileStorageAdapter
    from ccserver.session import SessionManager

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    storage = FileStorageAdapter(sessions_dir)
    sm = SessionManager(
        base_dir=sessions_dir,
        project_root=other,
        storage=storage,
    )
    assert sm.project_root == other
