"""
tests/test_run_mode.py — RunMode 运行时权限确认测试

覆盖：
  - run_mode 解析（auto/interactive/默认值/非法值）— 走新配置加载器
  - ask 解析
  - auto 模式：ask 中的工具直接拒绝
  - interactive 模式：ask 中的工具发起 permission_request
  - 子代理始终强制 auto 模式
  - FilterEmitter 透传 permission_request 和 ask_user 事件
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from ccserver.agent import Agent, AgentContext
from ccserver.configuration.schema import CcServerConfig
from ccserver.configuration.loader import ProcessConfig, resolve_session
from ccserver.emitters import BaseEmitter
from ccserver.emitters.filter import FilterEmitter
from ccserver.messages import UnifiedToolCall


# ─── 辅助 ────────────────────────────────────────────────────────────────────


def _make_settings(ask: list[str] | None = None, run_mode: str = "auto") -> CcServerConfig:
    """构建带 run_mode / ask 的 CcServerConfig（取代旧 ProjectSettings）。"""
    return CcServerConfig.from_dict({
        "agent": {"run_mode": run_mode},
        "permissions": {"ask": list(ask) if ask else []},
    })


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
    settings: CcServerConfig | None = None,
    run_mode: str | None = None,
) -> Agent:
    session = MagicMock()
    session.config = settings or _make_settings()
    session.mcp.schemas.return_value = []
    session.skills = MagicMock()
    session.project_root = project_root
    session.hooks.emit = AsyncMock(return_value=_make_hook_result(block=False))
    session.hooks.emit_void = AsyncMock(return_value=None)
    session.event_bus.publish = AsyncMock(return_value=None)

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


# ─── run_mode / ask 解析（新配置加载器，nested 格式）─────────────────────────


def _write_project(tmp_path: Path, data: dict) -> Path:
    ccserver = tmp_path / ".ccserver"
    ccserver.mkdir(exist_ok=True)
    (ccserver / "settings.local.json").write_text(json.dumps(data), encoding="utf-8")
    return ccserver / "settings.local.json"


def _resolve(tmp_path: Path, data: dict | None = None) -> CcServerConfig:
    pf = _write_project(tmp_path, data) if data is not None else tmp_path / "none.json"
    pc = ProcessConfig.load(global_file=tmp_path / "none-global.json", environ={})
    return resolve_session(pc, project_file=pf)


def test_run_mode_default_auto(tmp_path):
    cfg = _resolve(tmp_path)
    assert cfg.agent.run_mode == "auto"


def test_run_mode_interactive_from_file(tmp_path):
    cfg = _resolve(tmp_path, {"agent": {"run_mode": "interactive"}})
    assert cfg.agent.run_mode == "interactive"


def test_run_mode_invalid_falls_back_to_auto(tmp_path):
    cfg = _resolve(tmp_path, {"agent": {"run_mode": "something_invalid"}})
    assert cfg.agent.run_mode == "auto"


def test_ask_tools_parsed(tmp_path):
    cfg = _resolve(tmp_path, {"permissions": {"ask": ["Bash", "WriteFile"]}})
    ask = cfg.permissions.ask_tools()
    assert "Bash" in ask
    assert "WriteFile" in ask


def test_ask_tools_default_empty(tmp_path):
    cfg = _resolve(tmp_path)
    assert cfg.permissions.ask_tools() == frozenset()


# ─── Agent.run_mode 初始化 ───────────────────────────────────────────────────


def test_agent_run_mode_from_settings(tmp_path):
    settings = _make_settings(run_mode="interactive")
    agent = _make_agent({}, tmp_path, settings)
    assert agent.run_mode == "interactive"


def test_agent_run_mode_explicit_override(tmp_path):
    # 即使 config 是 interactive，显式传 auto 也生效
    settings = _make_settings(run_mode="interactive")
    agent = _make_agent({}, tmp_path, settings, run_mode="auto")
    assert agent.run_mode == "auto"


def test_child_agent_always_auto(tmp_path):
    settings = _make_settings(run_mode="interactive")
    agent = _make_agent({}, tmp_path, settings)
    child = agent.spawn_child("do something")
    assert child.run_mode == "auto"


# ─── auto / interactive 模式工具确认 ────────────────────────────────────────


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_perm_session(tmp_path, settings):
    """构建带 permission 行为的 mock session。"""
    session = MagicMock()
    session.config = settings
    session.mcp.schemas.return_value = []
    session.skills = MagicMock()
    session.project_root = tmp_path
    session.hooks.emit = AsyncMock(return_value=_make_hook_result(block=False))
    session.hooks.emit_void = AsyncMock(return_value=None)
    session.event_bus.publish = AsyncMock(return_value=None)
    return session


def _make_perm_emitter(grant: bool):
    emitter = MagicMock(spec=BaseEmitter)
    emitter.emit_permission_request = AsyncMock(return_value=grant)
    emitter.emit_tool_start = AsyncMock()
    emitter.emit_tool_result = AsyncMock()
    return emitter


def test_auto_mode_ask_tool_denied(tmp_path):
    """auto 模式下，ask 中的工具调用应被直接拒绝（不调用 emit_permission_request）"""
    settings = _make_settings(ask=["Bash"], run_mode="auto")
    session = _make_perm_session(tmp_path, settings)
    emitter = _make_perm_emitter(grant=True)

    context = AgentContext(name="test", messages=[], depth=0)
    agent = Agent(
        session=session, adapter=MagicMock(), emitter=emitter, tools={},
        context=context, prompt_version="cc_reverse:v2.1.81", run_mode="auto",
    )

    blocks = [UnifiedToolCall(id="abc", name="Bash", input={"command": "ls"})]
    results, _ = _run(agent._handle_tools(blocks))

    emitter.emit_permission_request.assert_not_called()
    assert len(results) == 1
    # R3 S4：handle 返回 ToolResultBlock（类型化），按属性断言
    assert results[0].is_error is True
    assert "auto" in results[0].content


def test_auto_mode_non_ask_tool_not_blocked(tmp_path):
    """auto 模式下，不在 ask 中的工具正常执行"""
    settings = _make_settings(ask=["WriteFile"], run_mode="auto")

    bash_result = MagicMock()
    bash_result.content = "ok"
    bash_result.is_error = False
    bash_result.to_api_dict = MagicMock(return_value={"type": "tool_result", "tool_use_id": "abc", "content": "ok", "is_error": False})
    bash_tool = AsyncMock(return_value=bash_result)

    session = _make_perm_session(tmp_path, settings)
    emitter = _make_perm_emitter(grant=True)

    context = AgentContext(name="test", messages=[], depth=0)
    agent = Agent(
        session=session, adapter=MagicMock(), emitter=emitter, tools={"Bash": bash_tool},
        context=context, prompt_version="cc_reverse:v2.1.81", run_mode="auto",
    )

    blocks = [UnifiedToolCall(id="abc", name="Bash", input={"command": "ls"})]
    results, _ = _run(agent._handle_tools(blocks))

    emitter.emit_permission_request.assert_not_called()
    bash_tool.assert_called_once()


def test_interactive_mode_ask_tool_granted(tmp_path):
    """interactive 模式，用户批准 → 工具正常执行"""
    settings = _make_settings(ask=["Bash"], run_mode="interactive")

    bash_result = MagicMock()
    bash_result.content = "command output"
    bash_result.is_error = False
    bash_result.to_api_dict = MagicMock(return_value={"type": "tool_result", "tool_use_id": "abc", "content": "command output", "is_error": False})
    bash_tool = AsyncMock(return_value=bash_result)

    session = _make_perm_session(tmp_path, settings)
    emitter = _make_perm_emitter(grant=True)

    context = AgentContext(name="test", messages=[], depth=0)
    agent = Agent(
        session=session, adapter=MagicMock(), emitter=emitter, tools={"Bash": bash_tool},
        context=context, prompt_version="cc_reverse:v2.1.81", run_mode="interactive",
    )

    blocks = [UnifiedToolCall(id="abc", name="Bash", input={"command": "ls"})]
    results, _ = _run(agent._handle_tools(blocks))

    emitter.emit_permission_request.assert_called_once_with("Bash", {"command": "ls"})
    bash_tool.assert_called_once()


def test_interactive_mode_ask_tool_denied(tmp_path):
    """interactive 模式，用户拒绝 → 工具不执行，返回错误"""
    settings = _make_settings(ask=["Bash"], run_mode="interactive")

    bash_tool = MagicMock()
    bash_tool.__call__ = AsyncMock()

    session = _make_perm_session(tmp_path, settings)
    emitter = _make_perm_emitter(grant=False)

    context = AgentContext(name="test", messages=[], depth=0)
    agent = Agent(
        session=session, adapter=MagicMock(), emitter=emitter, tools={"Bash": bash_tool},
        context=context, prompt_version="cc_reverse:v2.1.81", run_mode="interactive",
    )

    blocks = [UnifiedToolCall(id="abc", name="Bash", input={"command": "rm -rf ."})]
    results, _ = _run(agent._handle_tools(blocks))

    emitter.emit_permission_request.assert_called_once()
    bash_tool.assert_not_called()
    assert len(results) == 1
    # R3 S4：handle 返回 ToolResultBlock（类型化），按属性断言
    assert results[0].is_error is True
    assert "denied" in results[0].content


# ─── FilterEmitter 透传 permission_request ───────────────────────────────────


def test_filter_emitter_final_only_blocks_intermediate_events():
    """final_only + stream=False 时，只透传 done/error，过滤 token/tool_start"""

    class RecordEmitter(BaseEmitter):
        def __init__(self):
            self.events = []

        async def emit(self, event: dict) -> None:
            self.events.append(event)

    inner = RecordEmitter()
    fe = FilterEmitter(inner, verbosity="final_only", stream=False)

    _run(fe.emit({"type": "token", "content": "hello"}))
    _run(fe.emit({"type": "tool_start", "tool": "Bash", "preview": "ls"}))
    _run(fe.emit({"type": "done", "content": "done"}))

    event_types = [e["type"] for e in inner.events]
    assert "done" in event_types
    assert "token" not in event_types
    assert "tool_start" not in event_types


def test_filter_emitter_delegates_emit_ask_user():
    """emit_ask_user 必须委托给内部 emitter"""

    class MockInner(BaseEmitter):
        def __init__(self):
            self.called = False
            self.result = "user answer"

        async def emit(self, event: dict) -> None:
            pass

        async def emit_ask_user(self, questions: list) -> str:
            self.called = True
            return self.result

    inner = MockInner()
    fe = FilterEmitter(inner, verbosity="verbose", interactive=True)
    result = _run(fe.emit_ask_user([{"question": "What?"}]))

    assert inner.called
    assert result == "user answer"


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
    fe = FilterEmitter(inner, verbosity="verbose", interactive=True)
    result = _run(fe.emit_permission_request("Bash", {"command": "ls"}))

    assert inner.called
    assert result is True
