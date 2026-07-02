"""
test_config_permissions — 验证权限/端点判定方法（从旧 ProjectSettings 迁移，行为对齐）。

对应 plan Task A5。这些断言取自旧 tests/test_settings.py 的核心不变式。
"""

from ccserver.configuration.schema import CcServerConfig


def test_tool_deny_wins():
    """内置工具：deny 优先，未 deny 默认允许。"""
    cfg = CcServerConfig.from_dict({"permissions": {"deny": ["Bash"]}})
    assert cfg.permissions.is_tool_allowed("Bash") is False
    assert cfg.permissions.is_tool_allowed("Read") is True


def test_tool_no_allow_constraint_on_builtin():
    """内置工具不受 allow 白名单约束（allow 只影响命令/MCP）。"""
    cfg = CcServerConfig.from_dict({"permissions": {"allow": ["Bash(git:*)"]}})
    assert cfg.permissions.is_tool_allowed("Write") is True


def test_mcp_whitelist_semantics():
    """MCP 工具保留白名单语义：只有 allow 列出的才允许。"""
    cfg = CcServerConfig.from_dict({"permissions": {"allow": ["mcp__s__t"]}})
    assert cfg.permissions.is_mcp_tool_allowed("mcp__s__t") is True
    assert cfg.permissions.is_mcp_tool_allowed("mcp__s__other") is False


def test_mcp_no_allow_means_all_allowed():
    """allow 为空（None）时 MCP 工具默认全部允许。"""
    cfg = CcServerConfig()
    assert cfg.permissions.is_mcp_tool_allowed("mcp__s__t") is True


def test_command_prefix_allow():
    """命令前缀白名单。"""
    cfg = CcServerConfig.from_dict({"permissions": {"allow": ["Bash(git:*)"]}})
    assert cfg.permissions.is_command_allowed("Bash", "git status") is True
    assert cfg.permissions.is_command_allowed("Bash", "rm -rf /") is False


def test_command_deny_prefix():
    """命令前缀黑名单优先。"""
    cfg = CcServerConfig.from_dict({"permissions": {"deny": ["Bash(rm:*)"]}})
    assert cfg.permissions.is_command_allowed("Bash", "rm -rf /") is False
    assert cfg.permissions.is_command_allowed("Bash", "ls") is True


def test_filter_tools():
    """filter_tools 过滤掉被 deny 的工具。"""
    cfg = CcServerConfig.from_dict({"permissions": {"deny": ["Bash"]}})
    out = cfg.permissions.filter_tools({"Bash": 1, "Read": 2})
    assert out == {"Read": 2}


def test_to_model_endpoint():
    """ModelConfig 可构造 ModelEndpoint。"""
    cfg = CcServerConfig.from_dict(
        {"model": {"model_id": "m", "api_type": "anthropic-messages"}}
    )
    ep = cfg.model.to_model_endpoint()
    assert ep.model_id == "m"
    assert ep.api_type == "anthropic-messages"


def test_to_model_endpoint_override_model_id():
    """to_model_endpoint 可覆盖 model_id。"""
    cfg = CcServerConfig()
    ep = cfg.model.to_model_endpoint(model_id="other")
    assert ep.model_id == "other"
