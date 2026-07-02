# tests/test_session_project_root.py


def test_session_manager_uses_project_dir(tmp_path):
    """不传 project_root 时，SessionManager 应使用 process_config.infra.project_dir。

    特意把 project_dir 设置成与 base_dir.parent 不同的路径，
    以区分两种实现：base_dir.parent vs config.infra.project_dir。
    """
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    from ccserver.storage import FileStorageAdapter
    from ccserver.session import SessionManager
    from ccserver.configuration.loader import ProcessConfig

    # 通过环境变量驱动新配置系统（CCSERVER_PROJECT_DIR → infra.project_dir）
    pc = ProcessConfig.load(
        global_file=tmp_path / "none.json",
        environ={"CCSERVER_PROJECT_DIR": str(project_dir.resolve())},
    )

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    storage = FileStorageAdapter(sessions_dir)
    sm = SessionManager(
        base_dir=sessions_dir,
        storage=storage,
        process_config=pc,
    )
    assert sm.project_root == project_dir.resolve()


def test_session_manager_explicit_project_root(tmp_path):
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
