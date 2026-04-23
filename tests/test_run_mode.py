"""
tests/test_run_mode.py — RunMode 运行时权限确认测试

覆盖：
  - settings.run_mode 解析（auto/interactive/默认值/非法值）
  - settings.ask_tools 解析（全局+项目合并取并集）
  - auto 模式：ask_tools 中的工具直接拒绝
  - interactive 模式：ask_tools 中的工具发起 permission_request
  - 子代理始终强制 auto 模式
  - FilterEmitter 透传 permission_request 和 ask_user 事件
"""

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from ccserver.agent import Agent, AgentContext
from ccserver.managers.agents import AgentDef
from ccserver.settings import ProjectSettings
from ccserver.emitters import BaseEmitter
from ccserver.emitters.filter import FilterEmitter


# ─── 辅助 ────────────────────────────────────────────────────────────────────


def _make_settings(
    ask: list[str] | None = None,
    run_mode: str = "auto",
) -> ProjectSettings:
    return ProjectSettings(
        allowed_tools=None,
        denied_tools=frozenset(),
        allowed_commands=None,
        denied_commands={},
        enabled_mcp_servers=None,
        ask_tools=frozenset(ask) if ask else frozenset(),
        run_mode=run_mode,
    )


def _make_hook_result(block=False, permission_behavior="passthrough"):
    """返回模拟 hook emit 结果，block=False 表示不阻断。"""
    r = MagicMock()
    r.block = block
    r.block_reason = ""
    r.permission_behavior = permission_behavior
    r.updated_input = None
    return r


def _make_agent(
    tools: dict,
    project_root: Path,
    settings: ProjectSettings | None = None,
    run_mode: str | None = None,
) -> Agent:
    session = MagicMock()
    session.settings = settings or _make_settings()
    session.mcp.schemas.return_value = []
    session.skills = MagicMock()
    session.project_root = project_root
    # hooks.emit 是 async，返回有 .block 属性的对象
    session.hooks.emit = AsyncMock(return_value=_make_hook_result(block=False))
    session.hooks.emit_void = AsyncMock(return_value=None)

    context = AgentContext(name="orchestrator", messages=[], depth=0)
    return Agent(
        session=session,
        adapter=MagicMock(),
        emitter=MagicMock(spec=BaseEmitter),
        tools=tools,
        context=context,
        prompt_version="cc_reverse:v2.1.81",
        run_mode=run_mode,
    )


# ─── settings.run_mode 解析 ─────────────────────────────────────────────────


def _write_settings_file(tmp_path: Path, data: dict, filename: str = "settings.local.json") -> Path:
    ccserver = tmp_path / ".ccserver"
    ccserver.mkdir(exist_ok=True)
    (ccserver / filename).write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


def test_run_mode_default_auto(tmp_path):
    s = ProjectSettings.from_dirs(tmp_path)
    assert s.run_mode == "auto"


def test_run_mode_interactive_from_file(tmp_path):
    _write_settings_file(tmp_path, {"runMode": "interactive"})
    s = ProjectSettings.from_dirs(tmp_path)
    assert s.run_mode == "interactive"


def test_run_mode_auto_explicit(tmp_path):
    _write_settings_file(tmp_path, {"runMode": "auto"})
    s = ProjectSettings.from_dirs(tmp_path)
    assert s.run_mode == "auto"


def test_run_mode_invalid_falls_back_to_auto(tmp_path):
    _write_settings_file(tmp_path, {"runMode": "something_invalid"})
    s = ProjectSettings.from_dirs(tmp_path)
    assert s.run_mode == "auto"


def test_run_mode_project_overrides_global(tmp_path, monkeypatch):
    home_tmp = tmp_path / "home"
    home_tmp.mkdir()
    project_tmp = tmp_path / "project"
    project_tmp.mkdir()

    (home_tmp / ".ccserver").mkdir(exist_ok=True)
    (home_tmp / ".ccserver" / "settings.json").write_text(
        json.dumps({"runMode": "interactive"}), encoding="utf-8"
    )
    _write_settings_file(project_tmp, {"runMode": "auto"})

    monkeypatch.setenv("HOME", str(home_tmp))
    import importlib, ccserver.settings as sm
    importlib.reload(sm)

    s = sm.ProjectSettings.from_dirs(project_tmp)
    assert s.run_mode == "auto"


# ─── settings.ask_tools 解析 ────────────────────────────────────────────────


def test_ask_tools_parsed(tmp_path):
    _write_settings_file(tmp_path, {"permissions": {"ask": ["Bash", "WriteFile"]}})
    s = ProjectSettings.from_dirs(tmp_path)
    assert "Bash" in s.ask_tools
    assert "WriteFile" in s.ask_tools


def test_ask_tools_default_empty(tmp_path):
    s = ProjectSettings.from_dirs(tmp_path)
    assert s.ask_tools == frozenset()


def test_ask_tools_global_and_project_union(tmp_path, monkeypatch):
    home_tmp = tmp_path / "home"
    home_tmp.mkdir()
    project_tmp = tmp_path / "project"
    project_tmp.mkdir()

    (home_tmp / ".ccserver").mkdir(exist_ok=True)
    (home_tmp / ".ccserver" / "settings.json").write_text(
        json.dumps({"permissions": {"ask": ["Bash"]}}), encoding="utf-8"
    )
    _write_settings_file(project_tmp, {"permissions": {"ask": ["WriteFile"]}})

    monkeypatch.setenv("HOME", str(home_tmp))
    import importlib, ccserver.settings as sm
    importlib.reload(sm)

    s = sm.ProjectSettings.from_dirs(project_tmp)
    assert "Bash" in s.ask_tools
    assert "WriteFile" in s.ask_tools


# ─── Agent.run_mode 初始化 ───────────────────────────────────────────────────


def test_agent_run_mode_from_settings(tmp_path):
    settings = _make_settings(run_mode="interactive")
    agent = _make_agent({}, tmp_path, settings)
    assert agent.run_mode == "interactive"


def test_agent_run_mode_explicit_override(tmp_path):
    # 即使 settings 是 interactive，显式传 auto 也生效
    settings = _make_settings(run_mode="interactive")
    agent = _make_agent({}, tmp_path, settings, run_mode="auto")
    assert agent.run_mode == "auto"


def test_child_agent_always_auto(tmp_path):
    settings = _make_settings(run_mode="interactive")
    agent = _make_agent({}, tmp_path, settings)
    child = agent.spawn_child("do something")
    assert child.run_mode == "auto"


# ─── auto 模式：ask_tools 直接拒绝 ──────────────────────────────────────────


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_auto_mode_ask_tool_denied(tmp_path):
    """auto 模式下，ask_tools 中的工具调用应被直接拒绝（不调用 emit_permission_request）"""
    settings = _make_settings(ask=["Bash"], run_mode="auto")

    session = MagicMock()
    session.settings = settings
    session.mcp.schemas.return_value = []
    session.skills = MagicMock()
    session.project_root = tmp_path
    session.hooks.emit = AsyncMock(return_value=_make_hook_result(block=False))
    session.hooks.emit_void = AsyncMock(return_value=None)

    emitter = MagicMock(spec=BaseEmitter)
    emitter.emit_permission_request = AsyncMock(return_value=True)
    emitter.emit_tool_start = AsyncMock()
    emitter.emit_tool_result = AsyncMock()

    context = AgentContext(name="test", messages=[], depth=0)
    agent = Agent(
        session=session,
        adapter=MagicMock(),
        emitter=emitter,
        tools={},
        context=context,
        prompt_version="cc_reverse:v2.1.81",
        run_mode="auto",
    )

    # 构造一个 Bash tool_use block
    blocks = [{"type": "tool_use", "id": "abc", "name": "Bash", "input": {"command": "ls"}}]
    results, _ = _run(agent._handle_tools(blocks))

    # auto 模式：不应调用 emit_permission_request
    emitter.emit_permission_request.assert_not_called()
    # 应该有一个错误结果
    assert len(results) == 1
    assert results[0]["is_error"] is True
    assert "auto" in results[0]["content"]


def test_auto_mode_non_ask_tool_not_blocked(tmp_path):
    """auto 模式下，不在 ask_tools 中的工具正常执行"""
    settings = _make_settings(ask=["WriteFile"], run_mode="auto")

    bash_result = MagicMock()
    bash_result.content = "ok"
    bash_result.is_error = False
    bash_result.to_api_dict = MagicMock(return_value={"type": "tool_result", "tool_use_id": "abc", "content": "ok", "is_error": False})
    bash_tool = AsyncMock(return_value=bash_result)

    session = MagicMock()
    session.settings = settings
    session.mcp.schemas.return_value = []
    session.skills = MagicMock()
    session.project_root = tmp_path
    session.hooks.emit = AsyncMock(return_value=_make_hook_result(block=False))
    session.hooks.emit_void = AsyncMock(return_value=None)

    emitter = MagicMock(spec=BaseEmitter)
    emitter.emit_permission_request = AsyncMock(return_value=True)
    emitter.emit_tool_start = AsyncMock()
    emitter.emit_tool_result = AsyncMock()

    context = AgentContext(name="test", messages=[], depth=0)
    agent = Agent(
        session=session,
        adapter=MagicMock(),
        emitter=emitter,
        tools={"Bash": bash_tool},
        context=context,
        prompt_version="cc_reverse:v2.1.81",
        run_mode="auto",
    )

    blocks = [{"type": "tool_use", "id": "abc", "name": "Bash", "input": {"command": "ls"}}]
    results, _ = _run(agent._handle_tools(blocks))

    # Bash 不在 ask_tools 中，不应被拒绝
    emitter.emit_permission_request.assert_not_called()
    bash_tool.assert_called_once()


# ─── interactive 模式：ask_tools 等待确认 ────────────────────────────────────


def test_interactive_mode_ask_tool_granted(tmp_path):
    """interactive 模式，用户批准 → 工具正常执行"""
    settings = _make_settings(ask=["Bash"], run_mode="interactive")

    bash_result = MagicMock()
    bash_result.content = "command output"
    bash_result.is_error = False
    bash_result.to_api_dict = MagicMock(return_value={"type": "tool_result", "tool_use_id": "abc", "content": "command output", "is_error": False})
    bash_tool = AsyncMock(return_value=bash_result)

    session = MagicMock()
    session.settings = settings
    session.mcp.schemas.return_value = []
    session.skills = MagicMock()
    session.project_root = tmp_path
    session.hooks.emit = AsyncMock(return_value=_make_hook_result(block=False))
    session.hooks.emit_void = AsyncMock(return_value=None)

    emitter = MagicMock(spec=BaseEmitter)
    emitter.emit_permission_request = AsyncMock(return_value=True)   # 用户批准
    emitter.emit_tool_start = AsyncMock()
    emitter.emit_tool_result = AsyncMock()

    context = AgentContext(name="test", messages=[], depth=0)
    agent = Agent(
        session=session,
        adapter=MagicMock(),
        emitter=emitter,
        tools={"Bash": bash_tool},
        context=context,
        prompt_version="cc_reverse:v2.1.81",
        run_mode="interactive",
    )

    blocks = [{"type": "tool_use", "id": "abc", "name": "Bash", "input": {"command": "ls"}}]
    results, _ = _run(agent._handle_tools(blocks))

    # 应调用 emit_permission_request
    emitter.emit_permission_request.assert_called_once_with("Bash", {"command": "ls"})
    # 用户批准，工具应被执行
    bash_tool.assert_called_once()


def test_interactive_mode_ask_tool_denied(tmp_path):
    """interactive 模式，用户拒绝 → 工具不执行，返回错误"""
    settings = _make_settings(ask=["Bash"], run_mode="interactive")

    bash_tool = MagicMock()
    bash_tool.__call__ = AsyncMock()

    session = MagicMock()
    session.settings = settings
    session.mcp.schemas.return_value = []
    session.skills = MagicMock()
    session.project_root = tmp_path
    session.hooks.emit = AsyncMock(return_value=_make_hook_result(block=False))
    session.hooks.emit_void = AsyncMock(return_value=None)

    emitter = MagicMock(spec=BaseEmitter)
    emitter.emit_permission_request = AsyncMock(return_value=False)  # 用户拒绝
    emitter.emit_tool_start = AsyncMock()
    emitter.emit_tool_result = AsyncMock()

    context = AgentContext(name="test", messages=[], depth=0)
    agent = Agent(
        session=session,
        adapter=MagicMock(),
        emitter=emitter,
        tools={"Bash": bash_tool},
        context=context,
        prompt_version="cc_reverse:v2.1.81",
        run_mode="interactive",
    )

    blocks = [{"type": "tool_use", "id": "abc", "name": "Bash", "input": {"command": "rm -rf ."}}]
    results, _ = _run(agent._handle_tools(blocks))

    # 应调用 emit_permission_request
    emitter.emit_permission_request.assert_called_once()
    # 用户拒绝，工具不执行
    bash_tool.assert_not_called()
    # 结果是错误
    assert len(results) == 1
    assert results[0]["is_error"] is True
    assert "denied" in results[0]["content"]


# ─── FilterEmitter 透传 permission_request ───────────────────────────────────


def test_filter_emitter_passes_permission_request_in_final_only():
    """final_only 模式下，permission_request 事件必须透传，否则 agent 会永久挂起"""

    class RecordEmitter(BaseEmitter):
        def __init__(self):
            self.events = []

        async def emit(self, event: dict) -> None:
            self.events.append(event)

    inner = RecordEmitter()
    fe = FilterEmitter(inner, mode="final_only")

    _run(fe.emit({"type": "permission_request", "tool": "Bash", "input": {}}))
    _run(fe.emit({"type": "token", "content": "hello"}))
    _run(fe.emit({"type": "done", "content": "done"}))

    event_types = [e["type"] for e in inner.events]
    assert "permission_request" in event_types   # 必须透传
    assert "token" not in event_types             # final_only 过滤 token


def test_filter_emitter_passes_ask_user_in_streaming():
    """streaming 模式下，ask_user 事件也必须透传"""

    class RecordEmitter(BaseEmitter):
        def __init__(self):
            self.events = []

        async def emit(self, event: dict) -> None:
            self.events.append(event)

    inner = RecordEmitter()
    fe = FilterEmitter(inner, mode="streaming")

    _run(fe.emit({"type": "ask_user", "questions": []}))
    _run(fe.emit({"type": "tool_start", "tool": "Bash", "preview": "ls"}))

    event_types = [e["type"] for e in inner.events]
    assert "ask_user" in event_types       # 必须透传
    assert "tool_start" not in event_types  # streaming 过滤 tool_start


def test_filter_emitter_delegates_emit_permission_request():
    """FilterEmitter.emit_permission_request 必须委托给内部 emitter"""

    class MockInner(BaseEmitter):
        def __init__(self):
            self.called = False
            self.result = True

        async def emit(self, event: dict) -> None:
            pass

        async def emit_permission_request(self, tool_name: str, tool_input: dict) -> bool:
            self.called = True
            return self.result

    inner = MockInner()
    fe = FilterEmitter(inner, mode="final_only")
    result = _run(fe.emit_permission_request("Bash", {"command": "ls"}))

    assert inner.called
    assert result is True
