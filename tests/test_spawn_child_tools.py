"""
tests/test_spawn_child_tools.py — spawn_child() 分层工具过滤测试

覆盖：
  - CHILD_DISALLOWED_TOOLS 始终从子代理移除（AskUserQuestion、Compact、Agent）
  - 无 agent_def：使用 CHILD_DEFAULT_TOOLS 默认白名单
  - is_teammate=True：在默认白名单基础上叠加 TEAMMATE_EXTRA_TOOLS
  - agent_def.tools 显式白名单：只保留列出的工具
  - agent_def.disallowed_tools 黑名单：从白名单中剔除
  - settings.denied_tools：透传到子代理，高于 agent_def.tools
  - settings.allowed_tools：约束子代理白名单上限
  - 决策优先级：hardcode > settings.deny > agent_def.disallowed > agent_def.tools/default > settings.allow
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from ccserver.agent import Agent, AgentContext
from ccserver.agents.loader import AgentDef
from ccserver.settings import ProjectSettings
from ccserver.tools.constants import CHILD_DISALLOWED_TOOLS, CHILD_DEFAULT_TOOLS, TEAMMATE_EXTRA_TOOLS


# ─── 辅助函数 ─────────────────────────────────────────────────────────────────


ALL_TOOL_NAMES = [
    "Read", "Write", "Edit", "Glob", "Grep", "Bash",
    "WebSearch", "WebFetch",
    "TaskCreate", "TaskUpdate", "TaskGet", "TaskList",
    "AskUserQuestion", "Compact", "Agent",
]


def _make_tools(*names: str) -> dict:
    return {name: MagicMock() for name in names}


def _make_settings(
    allowed: list[str] | None = None,
    denied: list[str] | None = None,
) -> ProjectSettings:
    return ProjectSettings(
        allowed_tools=frozenset(allowed) if allowed is not None else None,
        denied_tools=frozenset(denied) if denied is not None else frozenset(),
        allowed_commands=None,
        denied_commands={},
        enabled_mcp_servers=None,
    )


def _make_agent(tools: dict, project_root: Path, settings: ProjectSettings | None = None) -> Agent:
    session = MagicMock()
    session.settings = settings or _make_settings()
    session.mcp.schemas.return_value = []
    session.skills = MagicMock()
    # 必须是真实 Path，prompts_lib 会访问 session.project_root / "CLAUDE.md"
    # 使用不含 CLAUDE.md 的临时目录，lib 直接返回空字符串
    session.project_root = project_root

    context = AgentContext(name="orchestrator", messages=[], depth=0)
    return Agent(
        session=session,
        adapter=MagicMock(),
        emitter=MagicMock(),
        tools=tools,
        context=context,
        prompt_version="cc_reverse:v2.1.81",
    )


def _spawn(agent: Agent, agent_def: AgentDef | None = None) -> set:
    child = agent.spawn_child("do something", agent_def=agent_def)
    return set(child.tools.keys())


# ─── CHILD_DISALLOWED_TOOLS：始终移除 ────────────────────────────────────────


def test_child_never_gets_ask_user_question(tmp_path):
    agent = _make_agent(_make_tools(*ALL_TOOL_NAMES), tmp_path)
    assert "AskUserQuestion" not in _spawn(agent)


def test_child_never_gets_compact(tmp_path):
    agent = _make_agent(_make_tools(*ALL_TOOL_NAMES), tmp_path)
    assert "Compact" not in _spawn(agent)


def test_child_never_gets_agent_tool(tmp_path):
    agent = _make_agent(_make_tools(*ALL_TOOL_NAMES), tmp_path)
    assert "Agent" not in _spawn(agent)


def test_child_disallowed_cannot_be_overridden_by_agent_def(tmp_path):
    # 即使 agent_def.tools 显式列出了 AskUserQuestion，也不应出现在子代理工具集中
    agent = _make_agent(_make_tools(*ALL_TOOL_NAMES), tmp_path)
    ad = AgentDef(
        name="bad", description="test", system="", location=Path("."),
        tools=["Read", "AskUserQuestion", "Compact", "Agent"],
    )
    child_tools = _spawn(agent, ad)
    assert "AskUserQuestion" not in child_tools
    assert "Compact" not in child_tools
    assert "Agent" not in child_tools
    assert "Read" in child_tools


# ─── 无 agent_def：CHILD_DEFAULT_TOOLS 默认白名单 ────────────────────────────


def test_no_agent_def_uses_default_whitelist(tmp_path):
    tools = _make_tools(*ALL_TOOL_NAMES)
    agent = _make_agent(tools, tmp_path)
    child_tools = _spawn(agent)
    for name in CHILD_DEFAULT_TOOLS:
        if name in tools:
            assert name in child_tools, f"{name} 应在默认白名单中"


def test_no_agent_def_excludes_task_tools(tmp_path):
    tools = _make_tools(*ALL_TOOL_NAMES)
    agent = _make_agent(tools, tmp_path)
    child_tools = _spawn(agent)
    for name in TEAMMATE_EXTRA_TOOLS:
        assert name not in child_tools, f"{name} 不应在普通子代理工具集中"


# ─── is_teammate：叠加 TEAMMATE_EXTRA_TOOLS ───────────────────────────────────


def test_teammate_gets_task_tools(tmp_path):
    tools = _make_tools(*ALL_TOOL_NAMES)
    agent = _make_agent(tools, tmp_path)
    ad = AgentDef(name="tm", description="test", system="", location=Path("."),
                  is_teammate=True)
    child_tools = _spawn(agent, ad)
    for name in TEAMMATE_EXTRA_TOOLS:
        if name in tools:
            assert name in child_tools, f"Teammate 应有 {name}"


def test_non_teammate_no_task_tools_even_with_agent_def(tmp_path):
    tools = _make_tools(*ALL_TOOL_NAMES)
    agent = _make_agent(tools, tmp_path)
    ad = AgentDef(name="normal", description="test", system="", location=Path("."),
                  is_teammate=False)
    child_tools = _spawn(agent, ad)
    for name in TEAMMATE_EXTRA_TOOLS:
        assert name not in child_tools


# ─── agent_def.tools 显式白名单 ───────────────────────────────────────────────


def test_agent_def_tools_whitelist(tmp_path):
    agent = _make_agent(_make_tools(*ALL_TOOL_NAMES), tmp_path)
    ad = AgentDef(name="t", description="test", system="", location=Path("."),
                  tools=["Read", "Glob"])
    assert _spawn(agent, ad) == {"Read", "Glob"}


def test_agent_def_tools_not_in_parent_ignored(tmp_path):
    # 父工具集没有 "WebSearch"，即使白名单列出也不会出现
    agent = _make_agent(_make_tools("Read", "Bash"), tmp_path)
    ad = AgentDef(name="t", description="test", system="", location=Path("."),
                  tools=["Read", "WebSearch"])
    child_tools = _spawn(agent, ad)
    assert "WebSearch" not in child_tools
    assert "Read" in child_tools


# ─── agent_def.disallowed_tools 黑名单 ───────────────────────────────────────


def test_agent_def_disallowed_removes_from_whitelist(tmp_path):
    agent = _make_agent(_make_tools(*ALL_TOOL_NAMES), tmp_path)
    ad = AgentDef(name="t", description="test", system="", location=Path("."),
                  tools=["Read", "Bash", "Write"],
                  disallowed_tools=["Bash"])
    child_tools = _spawn(agent, ad)
    assert "Bash" not in child_tools
    assert "Read" in child_tools
    assert "Write" in child_tools


def test_agent_def_disallowed_on_default_whitelist(tmp_path):
    # 无 agent_def.tools，从默认白名单剔除 Bash
    agent = _make_agent(_make_tools(*ALL_TOOL_NAMES), tmp_path)
    ad = AgentDef(name="t", description="test", system="", location=Path("."),
                  disallowed_tools=["Bash"])
    child_tools = _spawn(agent, ad)
    assert "Bash" not in child_tools
    assert "Read" in child_tools


# ─── settings 约束透传 ────────────────────────────────────────────────────────


def test_settings_deny_applied_to_child(tmp_path):
    # 项目配置 deny Write，子代理也不应有 Write
    agent = _make_agent(_make_tools(*ALL_TOOL_NAMES), tmp_path, _make_settings(denied=["Write"]))
    assert "Write" not in _spawn(agent)


def test_settings_deny_overrides_agent_def_whitelist(tmp_path):
    # settings deny Bash，即使 agent_def.tools 列了 Bash，也不应出现
    agent = _make_agent(_make_tools(*ALL_TOOL_NAMES), tmp_path, _make_settings(denied=["Bash"]))
    ad = AgentDef(name="t", description="test", system="", location=Path("."),
                  tools=["Read", "Bash"])
    child_tools = _spawn(agent, ad)
    assert "Bash" not in child_tools
    assert "Read" in child_tools


def test_settings_allow_constrains_child(tmp_path):
    # settings allow 只有 Read，子代理默认白名单里其他工具也受限
    agent = _make_agent(_make_tools(*ALL_TOOL_NAMES), tmp_path, _make_settings(allowed=["Read"]))
    child_tools = _spawn(agent)
    assert "Read" in child_tools
    assert "Bash" not in child_tools
    assert "Write" not in child_tools


def test_settings_allow_none_no_constraint(tmp_path):
    # settings.allowed_tools=None 不限制子代理白名单
    tools = _make_tools(*ALL_TOOL_NAMES)
    agent = _make_agent(tools, tmp_path, _make_settings(allowed=None))
    child_tools = _spawn(agent)
    for name in CHILD_DEFAULT_TOOLS:
        if name in tools:
            assert name in child_tools


# ─── 优先级综合验证 ───────────────────────────────────────────────────────────


def test_priority_hardcode_beats_everything(tmp_path):
    # CHILD_DISALLOWED 优先于所有配置
    settings = _make_settings(allowed=["AskUserQuestion", "Compact", "Agent"])
    agent = _make_agent(_make_tools(*ALL_TOOL_NAMES), tmp_path, settings)
    ad = AgentDef(name="t", description="test", system="", location=Path("."),
                  tools=["Read", "AskUserQuestion", "Compact", "Agent"],
                  is_teammate=True)
    child_tools = _spawn(agent, ad)
    assert "AskUserQuestion" not in child_tools
    assert "Compact" not in child_tools
    assert "Agent" not in child_tools


def test_priority_settings_deny_beats_agent_def(tmp_path):
    # settings.deny > agent_def.tools
    settings = _make_settings(denied=["Write"])
    agent = _make_agent(_make_tools("Read", "Bash", "Write"), tmp_path, settings)
    ad = AgentDef(name="t", description="test", system="", location=Path("."),
                  tools=["Read", "Write"])
    child_tools = _spawn(agent, ad)
    assert "Write" not in child_tools
    assert "Read" in child_tools
