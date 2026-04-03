"""
tests/test_agent_loader.py — AgentDef 新字段解析测试

覆盖：
  - tools 字段不再混入 mcp__* 条目（bug 修复验证）
  - disallowed_tools 字段解析
  - is_teammate 字段解析（true/false/默认）
  - tools + mcp 字段正确拆分
"""

import pytest
from pathlib import Path

from ccserver.agents.loader import AgentLoader, AgentDef


def _write_agent(tmp_path: Path, filename: str, content: str) -> Path:
    agents_dir = tmp_path / ".ccserver" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    path = agents_dir / filename
    path.write_text(content, encoding="utf-8")
    return tmp_path


# ─── tools / mcp 拆分（bug 修复验证）────────────────────────────────────────


def test_tools_does_not_contain_mcp_entries(tmp_path):
    # 修复前：tools 字段混入了 mcp__* 条目
    # 修复后：tools 只存纯内置工具名，mcp__* 拆到 mcp 字段
    _write_agent(tmp_path, "test.md", """\
---
name: test-agent
description: 测试代理
tools:
  - ReadFile
  - Bash
  - mcp__web__search
  - mcp__web__news
---
system prompt
""")
    loader = AgentLoader(tmp_path / ".ccserver" / "agents")
    agent = loader.get("test-agent")
    assert agent is not None
    assert "mcp__web__search" not in agent.tools
    assert "mcp__web__news" not in agent.tools
    assert "ReadFile" in agent.tools
    assert "Bash" in agent.tools


def test_mcp_field_populated_from_tools(tmp_path):
    _write_agent(tmp_path, "test.md", """\
---
name: test-agent
description: 测试代理
tools:
  - ReadFile
  - mcp__web__search
  - mcp__web__news
---
system prompt
""")
    loader = AgentLoader(tmp_path / ".ccserver" / "agents")
    agent = loader.get("test-agent")
    assert agent.mcp is not None
    assert "mcp__web__search" in agent.mcp
    assert "mcp__web__news" in agent.mcp


def test_no_tools_field_gives_none(tmp_path):
    # 不填 tools → None（使用 CHILD_DEFAULT_TOOLS）
    _write_agent(tmp_path, "test.md", """\
---
name: test-agent
description: 测试代理
---
system prompt
""")
    loader = AgentLoader(tmp_path / ".ccserver" / "agents")
    agent = loader.get("test-agent")
    assert agent.tools is None
    assert agent.mcp is None


def test_only_mcp_in_tools_gives_none_tools(tmp_path):
    # tools 字段只有 mcp__* 条目，拆分后 basic_tools 为空 → tools=None
    _write_agent(tmp_path, "test.md", """\
---
name: test-agent
description: 测试代理
tools:
  - mcp__web__search
---
system prompt
""")
    loader = AgentLoader(tmp_path / ".ccserver" / "agents")
    agent = loader.get("test-agent")
    assert agent.tools is None
    assert agent.mcp == ["mcp__web__search"]


# ─── disallowed_tools 字段 ────────────────────────────────────────────────────


def test_disallowed_tools_parsed(tmp_path):
    _write_agent(tmp_path, "test.md", """\
---
name: test-agent
description: 只读探索，禁止写操作
disallowed_tools:
  - WriteFile
  - EditFile
  - Bash
---
system prompt
""")
    loader = AgentLoader(tmp_path / ".ccserver" / "agents")
    agent = loader.get("test-agent")
    assert agent.disallowed_tools is not None
    assert "WriteFile" in agent.disallowed_tools
    assert "EditFile" in agent.disallowed_tools
    assert "Bash" in agent.disallowed_tools


def test_no_disallowed_tools_gives_none(tmp_path):
    _write_agent(tmp_path, "test.md", """\
---
name: test-agent
description: 测试代理
---
system prompt
""")
    loader = AgentLoader(tmp_path / ".ccserver" / "agents")
    agent = loader.get("test-agent")
    assert agent.disallowed_tools is None


def test_disallowed_tools_inline_format(tmp_path):
    # 逗号分隔的内联格式
    _write_agent(tmp_path, "test.md", """\
---
name: test-agent
description: 测试代理
disallowed_tools: WriteFile, EditFile
---
system prompt
""")
    loader = AgentLoader(tmp_path / ".ccserver" / "agents")
    agent = loader.get("test-agent")
    assert agent.disallowed_tools is not None
    assert "WriteFile" in agent.disallowed_tools
    assert "EditFile" in agent.disallowed_tools


# ─── is_teammate 字段 ─────────────────────────────────────────────────────────


def test_is_teammate_default_false(tmp_path):
    _write_agent(tmp_path, "test.md", """\
---
name: test-agent
description: 普通子代理
---
system prompt
""")
    loader = AgentLoader(tmp_path / ".ccserver" / "agents")
    agent = loader.get("test-agent")
    assert agent.is_teammate is False


def test_is_teammate_true(tmp_path):
    _write_agent(tmp_path, "test.md", """\
---
name: test-agent
description: Teammate 角色
is_teammate: true
---
system prompt
""")
    loader = AgentLoader(tmp_path / ".ccserver" / "agents")
    agent = loader.get("test-agent")
    assert agent.is_teammate is True


def test_is_teammate_false_explicit(tmp_path):
    _write_agent(tmp_path, "test.md", """\
---
name: test-agent
description: 普通子代理
is_teammate: false
---
system prompt
""")
    loader = AgentLoader(tmp_path / ".ccserver" / "agents")
    agent = loader.get("test-agent")
    assert agent.is_teammate is False


# ─── 完整字段综合解析 ─────────────────────────────────────────────────────────


def test_full_agent_def_parse(tmp_path):
    _write_agent(tmp_path, "full.md", """\
---
name: full-agent
description: 完整字段测试
model: claude-haiku-4-5-20251001
is_teammate: true
tools:
  - ReadFile
  - Bash
  - mcp__web__search
disallowed_tools:
  - EditFile
skills:
  - my-skill
output_mode: final_only
---
这是 system prompt 正文
""")
    loader = AgentLoader(tmp_path / ".ccserver" / "agents")
    agent = loader.get("full-agent")
    assert agent is not None
    assert agent.model == "claude-haiku-4-5-20251001"
    assert agent.is_teammate is True
    assert agent.tools == ["ReadFile", "Bash"]      # mcp 已拆出
    assert agent.mcp == ["mcp__web__search"]
    assert agent.disallowed_tools == ["EditFile"]
    assert agent.skills == ["my-skill"]
    assert agent.output_mode == "final_only"
    assert agent.system == "这是 system prompt 正文"
