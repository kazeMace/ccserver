"""
tests/test_settings.py — ProjectSettings 权限解析与决策逻辑测试

覆盖：
  - allow / deny 条目解析（工具名 vs Bash(cmd:*) 格式）
  - 全局配置 + 项目配置合并规则
  - is_tool_allowed / is_command_allowed 决策顺序
  - filter_tools / filter_mcp_schemas
"""

import json
import pytest
from pathlib import Path

from ccserver.settings import ProjectSettings, _parse_entries


# ─── _parse_entries 单元测试 ──────────────────────────────────────────────────


def test_parse_entries_pure_tools():
    tools, cmds = _parse_entries(["WebSearch", "ReadFile", "mcp__web__search"])
    assert tools == frozenset({"WebSearch", "ReadFile", "mcp__web__search"})
    assert cmds == {}


def test_parse_entries_bash_commands():
    tools, cmds = _parse_entries(["Bash(git:*)", "Bash(pytest:*)"])
    assert tools == frozenset()
    assert cmds == {"Bash": ["git", "pytest"]}


def test_parse_entries_mixed():
    tools, cmds = _parse_entries(["WebSearch", "Bash(git:*)", "Bash(rm:*)", "ReadFile"])
    assert tools == frozenset({"WebSearch", "ReadFile"})
    assert cmds == {"Bash": ["git", "rm"]}


def test_parse_entries_empty():
    tools, cmds = _parse_entries([])
    assert tools == frozenset()
    assert cmds == {}


def test_parse_entries_strips_wildcard():
    # "Bash(git:*)" 应解析为前缀 "git"，去掉 ":*"
    _, cmds = _parse_entries(["Bash(git:*)"])
    assert cmds["Bash"] == ["git"]


# ─── 单层配置解析 ─────────────────────────────────────────────────────────────


def _write_settings(tmp_path: Path, data: dict, filename="settings.local.json") -> Path:
    ccserver = tmp_path / ".ccserver"
    ccserver.mkdir(exist_ok=True)
    path = ccserver / filename
    path.write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


def test_no_config_file_allows_everything(tmp_path):
    s = ProjectSettings.from_dirs(tmp_path)
    assert s.allowed_tools is None
    assert s.denied_tools == frozenset()
    assert s.allowed_commands is None
    assert s.denied_commands == {}
    assert s.is_tool_allowed("Bash") is True
    assert s.is_tool_allowed("WriteFile") is True


def test_allow_only(tmp_path):
    # 内置工具不受 allow 白名单约束，allow 只影响命令执行权限（如 Bash(cmd:*)）
    # 要禁用内置工具请用 deny
    _write_settings(tmp_path, {"permissions": {"allow": ["WebSearch", "ReadFile"]}})
    s = ProjectSettings.from_dirs(tmp_path)
    assert s.is_tool_allowed("WebSearch") is True
    assert s.is_tool_allowed("ReadFile") is True
    assert s.is_tool_allowed("WriteFile") is True    # 内置工具不受 allow 约束，默认允许


def test_deny_only(tmp_path):
    _write_settings(tmp_path, {"permissions": {"deny": ["WriteFile", "EditFile"]}})
    s = ProjectSettings.from_dirs(tmp_path)
    assert s.is_tool_allowed("WriteFile") is False   # 在黑名单
    assert s.is_tool_allowed("EditFile") is False    # 在黑名单
    assert s.is_tool_allowed("ReadFile") is True     # 不在黑名单，且无白名单限制


def test_deny_overrides_allow(tmp_path):
    # WriteFile 同时在 allow 和 deny，deny 应优先
    _write_settings(tmp_path, {"permissions": {
        "allow": ["WebSearch", "WriteFile"],
        "deny":  ["WriteFile"],
    }})
    s = ProjectSettings.from_dirs(tmp_path)
    assert s.is_tool_allowed("WriteFile") is False   # deny 优先
    assert s.is_tool_allowed("WebSearch") is True


def test_mcp_tool_allowed(tmp_path):
    _write_settings(tmp_path, {"permissions": {
        "allow": ["mcp__web__search_web"],
        "deny":  ["mcp__web__search_news"],
    }})
    s = ProjectSettings.from_dirs(tmp_path)
    assert s.is_mcp_tool_allowed("mcp__web__search_web") is True
    assert s.is_mcp_tool_allowed("mcp__web__search_news") is False
    assert s.is_mcp_tool_allowed("mcp__web__other") is False      # 不在白名单


def test_bash_command_allow(tmp_path):
    _write_settings(tmp_path, {"permissions": {
        "allow": ["Bash(git:*)", "Bash(pytest:*)"],
    }})
    s = ProjectSettings.from_dirs(tmp_path)
    assert s.is_command_allowed("Bash", "git status") is True
    assert s.is_command_allowed("Bash", "pytest tests/") is True
    assert s.is_command_allowed("Bash", "ls -la") is False         # 不在白名单


def test_bash_command_deny(tmp_path):
    _write_settings(tmp_path, {"permissions": {
        "deny": ["Bash(rm:*)"],
    }})
    s = ProjectSettings.from_dirs(tmp_path)
    assert s.is_command_allowed("Bash", "rm -rf .") is False       # 命中 deny
    assert s.is_command_allowed("Bash", "ls -la") is True          # 无白名单限制


def test_bash_command_deny_overrides_allow(tmp_path):
    _write_settings(tmp_path, {"permissions": {
        "allow": ["Bash(rm:*)"],
        "deny":  ["Bash(rm:*)"],
    }})
    s = ProjectSettings.from_dirs(tmp_path)
    assert s.is_command_allowed("Bash", "rm file.txt") is False    # deny 优先


def test_bash_hardblock_not_in_settings(tmp_path):
    # settings 没有限制时，硬编码黑名单由 BTBash.validate() 处理，不在 settings 层
    s = ProjectSettings.from_dirs(tmp_path)
    # settings 层不知道 _HARDBLOCK，这里 is_command_allowed 返回 True
    # 真正的拦截在 BTBash.validate()
    assert s.is_command_allowed("Bash", "rm -rf /") is True


# ─── 全局 + 项目配置合并 ───────────────────────────────────────────────────────


def _write_global_settings(home_tmp: Path, data: dict) -> Path:
    ccserver = home_tmp / ".ccserver"
    ccserver.mkdir(exist_ok=True)
    (ccserver / "settings.json").write_text(json.dumps(data), encoding="utf-8")
    return home_tmp


def test_merge_deny_union(tmp_path, monkeypatch):
    # 全局 deny WriteFile，项目 deny EditFile，合并后两者都被拒绝
    home_tmp = tmp_path / "home"
    home_tmp.mkdir()
    project_tmp = tmp_path / "project"
    project_tmp.mkdir()

    _write_global_settings(home_tmp, {"permissions": {"deny": ["WriteFile"]}})
    _write_settings(project_tmp, {"permissions": {"deny": ["EditFile"]}})

    monkeypatch.setenv("HOME", str(home_tmp))
    # 重新加载以使 Path.home() 生效
    import importlib, ccserver.settings as sm
    importlib.reload(sm)

    s = sm.ProjectSettings.from_dirs(project_tmp)
    assert s.is_tool_allowed("WriteFile") is False   # 全局 deny
    assert s.is_tool_allowed("EditFile") is False    # 项目 deny
    assert s.is_tool_allowed("ReadFile") is True     # 两层都没禁


def test_merge_project_allow_overrides_global(tmp_path, monkeypatch):
    # 全局 allow WebSearch，项目 allow ReadFile（覆盖全局）
    # 内置工具不受 allow 白名单约束，is_tool_allowed 对内置工具只看 deny
    home_tmp = tmp_path / "home"
    home_tmp.mkdir()
    project_tmp = tmp_path / "project"
    project_tmp.mkdir()

    _write_global_settings(home_tmp, {"permissions": {"allow": ["WebSearch"]}})
    _write_settings(project_tmp, {"permissions": {"allow": ["ReadFile"]}})

    monkeypatch.setenv("HOME", str(home_tmp))
    import importlib, ccserver.settings as sm
    importlib.reload(sm)

    s = sm.ProjectSettings.from_dirs(project_tmp)
    # 内置工具不受 allow 约束，两者都允许
    assert s.is_tool_allowed("ReadFile") is True
    assert s.is_tool_allowed("WebSearch") is True
    # 但 allowed_tools 反映项目 allow 覆盖了全局（allowed_tools 只含项目 allow 中的条目）
    assert s.allowed_tools == frozenset({"ReadFile"})


def test_merge_global_allow_used_when_no_project_allow(tmp_path, monkeypatch):
    # 只有全局 allow，项目没有 allow，全局白名单生效
    # 内置工具不受 allow 约束，is_tool_allowed 只看 deny
    home_tmp = tmp_path / "home"
    home_tmp.mkdir()
    project_tmp = tmp_path / "project"
    project_tmp.mkdir()

    _write_global_settings(home_tmp, {"permissions": {"allow": ["WebSearch"]}})
    # 项目只有 deny，没有 allow
    _write_settings(project_tmp, {"permissions": {"deny": ["EditFile"]}})

    monkeypatch.setenv("HOME", str(home_tmp))
    import importlib, ccserver.settings as sm
    importlib.reload(sm)

    s = sm.ProjectSettings.from_dirs(project_tmp)
    assert s.is_tool_allowed("WebSearch") is True    # 内置工具不受 allow 约束，始终允许
    assert s.is_tool_allowed("ReadFile") is True     # 内置工具默认允许
    # MCP 工具才受 allow 白名单约束：allowed_tools 中只有 WebSearch
    assert s.is_mcp_tool_allowed("mcp__web__search") is False  # 不在 allow 列表
    assert s.allowed_tools == frozenset({"WebSearch"})


def test_merge_command_deny_union(tmp_path, monkeypatch):
    # 全局 deny Bash(rm:*)，项目 deny Bash(sudo:*)，合并后两者都拒绝
    home_tmp = tmp_path / "home"
    home_tmp.mkdir()
    project_tmp = tmp_path / "project"
    project_tmp.mkdir()

    _write_global_settings(home_tmp, {"permissions": {"deny": ["Bash(rm:*)"]}})
    _write_settings(project_tmp, {"permissions": {"deny": ["Bash(sudo:*)"]}})

    monkeypatch.setenv("HOME", str(home_tmp))
    import importlib, ccserver.settings as sm
    importlib.reload(sm)

    s = sm.ProjectSettings.from_dirs(project_tmp)
    assert s.is_command_allowed("Bash", "rm file") is False
    assert s.is_command_allowed("Bash", "sudo apt") is False
    assert s.is_command_allowed("Bash", "ls -la") is True


# ─── filter_tools / filter_mcp_schemas ───────────────────────────────────────


def test_filter_tools(tmp_path):
    # 内置工具：deny 优先，allow 不约束内置工具
    _write_settings(tmp_path, {"permissions": {
        "allow": ["ReadFile", "Bash"],
        "deny":  ["Bash"],
    }})
    s = ProjectSettings.from_dirs(tmp_path)
    from unittest.mock import MagicMock
    tools = {
        "ReadFile": MagicMock(),
        "WriteFile": MagicMock(),
        "Bash": MagicMock(),
    }
    result = s.filter_tools(tools)
    assert "ReadFile" in result
    assert "WriteFile" in result   # 内置工具不受 allow 约束，没有 deny 则允许
    assert "Bash" not in result    # deny 优先


def test_filter_mcp_schemas(tmp_path):
    _write_settings(tmp_path, {"permissions": {
        "allow": ["mcp__web__search"],
    }})
    s = ProjectSettings.from_dirs(tmp_path)
    schemas = [
        {"name": "mcp__web__search"},
        {"name": "mcp__web__news"},
    ]
    result = s.filter_mcp_schemas(schemas)
    assert len(result) == 1
    assert result[0]["name"] == "mcp__web__search"


def test_enabled_mcp_servers(tmp_path):
    _write_settings(tmp_path, {"enabledMcpjsonServers": ["web-search", "memory"]})
    s = ProjectSettings.from_dirs(tmp_path)
    assert s.is_mcp_server_enabled("web-search") is True
    assert s.is_mcp_server_enabled("memory") is True
    assert s.is_mcp_server_enabled("other") is False
