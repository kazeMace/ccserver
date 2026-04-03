# tests/test_config_project_dir.py
import importlib
from pathlib import Path
import ccserver.config


def _reload_config():
    """重新加载 config 模块（环境变量由 monkeypatch 管理，不在此处设置）。"""
    importlib.reload(ccserver.config)
    return ccserver.config


def test_project_dir_defaults_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CCSERVER_PROJECT_DIR", raising=False)
    config = _reload_config()
    assert config.PROJECT_DIR == tmp_path.resolve()


def test_project_dir_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CCSERVER_PROJECT_DIR", str(tmp_path))
    config = _reload_config()
    assert config.PROJECT_DIR == tmp_path.resolve()


def test_sessions_base_follows_global_config_dir(tmp_path, monkeypatch):
    # SESSIONS_BASE 默认跟 GLOBAL_CONFIG_DIR（~/.ccserver/sessions），不跟 PROJECT_DIR
    monkeypatch.setenv("CCSERVER_GLOBAL_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("CCSERVER_SESSIONS_DIR", raising=False)
    config = _reload_config()
    assert config.SESSIONS_BASE == tmp_path.resolve() / "sessions"


def test_log_dir_follows_global_config_dir(tmp_path, monkeypatch):
    # LOG_DIR 默认跟 GLOBAL_CONFIG_DIR（~/.ccserver/logs），不跟 PROJECT_DIR
    monkeypatch.setenv("CCSERVER_GLOBAL_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("CCSERVER_LOG_DIR", raising=False)
    config = _reload_config()
    assert config.LOG_DIR == tmp_path.resolve() / "logs"


def test_sessions_base_override(tmp_path, monkeypatch, tmp_path_factory):
    custom = tmp_path_factory.mktemp("custom_sessions")
    monkeypatch.setenv("CCSERVER_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("CCSERVER_SESSIONS_DIR", str(custom))
    config = _reload_config()
    assert config.SESSIONS_BASE == Path(str(custom))
