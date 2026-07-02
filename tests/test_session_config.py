"""
test_session_config — 验证 Session 持有 CcServerConfig（Phase B 接线）。

对应 plan Task B1。
"""

from ccserver.session import SessionManager
from ccserver.configuration.loader import ProcessConfig


def test_session_has_config(tmp_path):
    """Session 暴露 config 属性，类型为 CcServerConfig。"""
    pc = ProcessConfig.load(global_file=tmp_path / "g.json", environ={})
    sm = SessionManager(process_config=pc, project_root=tmp_path)
    sess = sm.create()
    assert hasattr(sess, "config")
    assert sess.config.model.model_id == "claude-sonnet-4-6"


def test_session_config_reads_project_file(tmp_path):
    """项目 settings.local.json 覆盖进程默认。"""
    import json
    ccserver_dir = tmp_path / ".ccserver"
    ccserver_dir.mkdir()
    (ccserver_dir / "settings.local.json").write_text(
        json.dumps({"agent": {"language": "English"}})
    )
    pc = ProcessConfig.load(global_file=tmp_path / "g.json", environ={})
    sm = SessionManager(process_config=pc, project_root=tmp_path)
    sess = sm.create()
    assert sess.config.agent.language == "English"


def test_session_manager_default_process_config(tmp_path):
    """不传 process_config 时自动 load（不报错）。"""
    sm = SessionManager(project_root=tmp_path)
    sess = sm.create()
    assert sess.config is not None
