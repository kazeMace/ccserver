"""
tests/test_bt_base.py — ToolResult、ToolParam、BaseTool 单元测试

覆盖：
  - ToolResult.ok() / .error() 工厂方法
  - ToolResult.to_api_dict() 序列化
  - ToolParam.to_property() schema 生成（含 enum/items）
  - BaseTool.to_schema() 完整 schema 输出
  - BaseTool.to_disabled_schema() 禁用占位 schema
  - BaseTool.validate() 必填参数检查
  - BaseTool.__call__() validate→run 调用链
  - 缺少 name/description 的子类抛异常
"""

import asyncio
import pytest
from pathlib import Path

from ccserver.builtins.tools import BuiltinTools, ToolParam, ToolResult


# ─── ToolResult ───────────────────────────────────────────────────────────────


def test_tool_result_ok():
    r = ToolResult.ok("success")
    assert r.content == "success"
    assert r.is_error is False


def test_tool_result_error():
    r = ToolResult.error("something failed")
    assert r.content == "something failed"
    assert r.is_error is True


def test_tool_result_to_api_dict():
    r = ToolResult.ok("output")
    d = r.to_api_dict("tool_123")
    assert d == {
        "type": "tool_result",
        "tool_use_id": "tool_123",
        "content": "output",
        "is_error": False,
    }


def test_tool_result_error_to_api_dict():
    r = ToolResult.error("bad")
    d = r.to_api_dict("tid")
    assert d["is_error"] is True
    assert d["content"] == "bad"


# ─── ToolParam ───────────────────────────────────────────────────────────────


def test_tool_param_to_property_basic():
    p = ToolParam(type="string", description="A string param")
    d = p.to_property()
    assert d == {"type": "string", "description": "A string param"}


def test_tool_param_to_property_with_enum():
    p = ToolParam(type="string", description="Mode", enum=["auto", "interactive"])
    d = p.to_property()
    assert d["enum"] == ["auto", "interactive"]


def test_tool_param_to_property_with_items_array():
    p = ToolParam(type="array", description="List of strings", items={"type": "string"})
    d = p.to_property()
    assert d["items"] == {"type": "string"}


def test_tool_param_items_ignored_for_non_array():
    # items 字段只对 type=="array" 生效
    p = ToolParam(type="string", description="Not array", items={"type": "string"})
    d = p.to_property()
    assert "items" not in d


def test_tool_param_required_default_true():
    p = ToolParam(type="integer", description="Count")
    assert p.required is True


def test_tool_param_optional():
    p = ToolParam(type="boolean", description="Flag", required=False)
    assert p.required is False


# ─── BaseTool 具体实现 ────────────────────────────────────────────────────────


class _SimpleTool(BuiltinTools):
    name = "SimpleTool"
    description = "A simple tool for testing."
    params = {
        "arg1": ToolParam(type="string", description="Required string"),
        "arg2": ToolParam(type="integer", description="Optional int", required=False),
    }

    async def run(self, arg1: str, arg2: int = 10) -> ToolResult:
        return ToolResult.ok(f"{arg1}:{arg2}")


class _ErrorTool(BuiltinTools):
    name = "ErrorTool"
    description = "A tool that always errors."
    params = {"x": ToolParam(type="string", description="Param")}

    async def run(self, x: str) -> ToolResult:
        return ToolResult.error("always fails")


class _RaisingTool(BuiltinTools):
    name = "RaisingTool"
    description = "A tool whose run() raises an exception."
    params = {"x": ToolParam(type="string", description="Param")}

    async def run(self, x: str) -> ToolResult:
        raise RuntimeError("unexpected error")


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── to_schema() ─────────────────────────────────────────────────────────────


def test_to_schema_structure():
    schema = _SimpleTool().to_schema()
    assert schema["name"] == "SimpleTool"
    assert schema["description"] == "A simple tool for testing."
    assert "input_schema" in schema
    assert schema["input_schema"]["type"] == "object"
    assert "arg1" in schema["input_schema"]["properties"]
    assert "arg2" in schema["input_schema"]["properties"]


def test_to_schema_required_only_includes_required_params():
    schema = _SimpleTool().to_schema()
    assert schema["input_schema"]["required"] == ["arg1"]
    assert "arg2" not in schema["input_schema"]["required"]


def test_to_schema_no_required_params_omits_required_key():
    class _AllOptional(BuiltinTools):
        name = "AllOptional"
        description = "All optional."
        params = {"x": ToolParam(type="string", description="...", required=False)}
        async def run(self, **kwargs): return ToolResult.ok("")

    schema = _AllOptional().to_schema()
    assert "required" not in schema["input_schema"]


def test_to_schema_missing_name_raises():
    class _NoName(BuiltinTools):
        name = ""
        description = "Has description."
        params = {}
        async def run(self, **kwargs): return ToolResult.ok("")

    with pytest.raises(NotImplementedError):
        _NoName().to_schema()


def test_to_schema_missing_description_raises():
    class _NoDesc(BuiltinTools):
        name = "NoDesc"
        description = ""
        params = {}
        async def run(self, **kwargs): return ToolResult.ok("")

    with pytest.raises(NotImplementedError):
        _NoDesc().to_schema()


# ─── to_disabled_schema() ────────────────────────────────────────────────────


def test_to_disabled_schema_has_name():
    schema = _SimpleTool().to_disabled_schema()
    assert schema["name"] == "SimpleTool"


def test_to_disabled_schema_no_required():
    schema = _SimpleTool().to_disabled_schema()
    assert schema["input_schema"]["properties"] == {}
    assert "required" not in schema["input_schema"]


def test_to_disabled_schema_description_mentions_tool():
    schema = _SimpleTool().to_disabled_schema()
    assert "SimpleTool" in schema["description"]


# ─── validate() ──────────────────────────────────────────────────────────────


def test_validate_missing_required_returns_error():
    tool = _SimpleTool()
    result = _run(tool.validate())  # 没有传 arg1
    assert result is not None
    assert result.is_error
    assert "arg1" in result.content


def test_validate_all_required_present_returns_none():
    tool = _SimpleTool()
    result = _run(tool.validate(arg1="hello"))
    assert result is None


def test_validate_optional_param_not_required():
    tool = _SimpleTool()
    result = _run(tool.validate(arg1="hello"))  # 不传 arg2（可选）
    assert result is None


# ─── __call__() ──────────────────────────────────────────────────────────────


def test_call_validates_and_runs():
    tool = _SimpleTool()
    result = _run(tool(arg1="foo", arg2=99))
    assert result.is_error is False
    assert result.content == "foo:99"


def test_call_returns_error_on_validation_fail():
    tool = _SimpleTool()
    result = _run(tool())  # 缺少 arg1
    assert result.is_error is True


def test_call_returns_error_from_run():
    tool = _ErrorTool()
    result = _run(tool(x="anything"))
    assert result.is_error is True
    assert "always fails" in result.content


def test_call_catches_unexpected_exception():
    tool = _RaisingTool()
    result = _run(tool(x="trigger"))
    assert result.is_error is True
    assert "RaisingTool" in result.content


# ─── __repr__() ──────────────────────────────────────────────────────────────


def test_repr_contains_name_and_params():
    r = repr(_SimpleTool())
    assert "SimpleTool" in r
    assert "arg1" in r
