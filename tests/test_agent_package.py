"""
test_agent_package — 文件夹 Agent Package 加载（对应 plan Task D1 / spec §7）。
"""

import json
from pathlib import Path

from ccserver.managers.agents.manager import AgentLoader


def test_load_folder_package_with_system_md(tmp_path):
    pkg = tmp_path / "web_search"
    pkg.mkdir()
    (pkg / "agent.json").write_text(json.dumps({
        "name": "web-search",
        "description": "搜索网络并返回结构化摘要",
        "model": "claude-haiku-4-5-20251001",
        "tools": ["Read", "WebSearch"],
    }), encoding="utf-8")
    (pkg / "system.md").write_text("你是一个搜索 agent", encoding="utf-8")

    ad = AgentLoader.load_package(pkg)
    assert ad is not None
    assert ad.name == "web-search"
    assert ad.system.strip() == "你是一个搜索 agent"
    assert ad.model == "claude-haiku-4-5-20251001"
    assert ad.tools == ["Read", "WebSearch"]


def test_load_folder_package_inline_system(tmp_path):
    pkg = tmp_path / "topic"
    pkg.mkdir()
    (pkg / "agent.json").write_text(json.dumps({
        "name": "topic",
        "description": "推荐话题",
        "system": "内联 system 文本",
    }), encoding="utf-8")

    ad = AgentLoader.load_package(pkg)
    assert ad is not None
    assert ad.system.strip() == "内联 system 文本"


def test_load_package_missing_json_returns_none(tmp_path):
    pkg = tmp_path / "empty"
    pkg.mkdir()
    assert AgentLoader.load_package(pkg) is None


def test_agent_def_overrides_maps_model(tmp_path):
    pkg = tmp_path / "a"
    pkg.mkdir()
    (pkg / "agent.json").write_text(json.dumps({
        "name": "a", "description": "d", "model": "gpt-4o",
    }), encoding="utf-8")
    ad = AgentLoader.load_package(pkg)
    ov = ad.overrides()
    assert ov["model"]["model_id"] == "gpt-4o"


async def test_create_root_with_agent_package(tmp_path):
    """create_root(agent_package=) 应用包的 name/model/system。"""
    import json
    from unittest.mock import MagicMock
    from ccserver.factory import AgentFactory
    from ccserver.session import SessionManager
    from ccserver.configuration.loader import ProcessConfig

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "agent.json").write_text(json.dumps({
        "name": "researcher", "description": "研究员",
        "model": "claude-haiku-4-5-20251001",
    }), encoding="utf-8")
    (pkg / "system.md").write_text("你是研究员", encoding="utf-8")

    proj = tmp_path / "proj"
    proj.mkdir()
    pc = ProcessConfig.load(global_file=tmp_path / "g.json", environ={})
    session = SessionManager(process_config=pc, project_root=proj).create()

    agent = AgentFactory.create_root(
        session, MagicMock(), adapter=MagicMock(), agent_package=str(pkg),
    )
    assert agent.context.name == "researcher"
    assert agent.model == "claude-haiku-4-5-20251001"
