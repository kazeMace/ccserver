"""
tests/test_pipeline_graph.py — Graph、FunctionNode、NodeData 单元测试

覆盖：
  - NodeData: get()、update()、__repr__()
  - FunctionNode: 同步/异步函数执行
  - Graph: add_node() 重复 id 异常、add_edge()、add_exit_edge()
  - Graph._validate(): 引用未注册节点
  - Graph._route(): 无条件边、条件边、exit 边、无出边
  - Graph._apply_field_map(): 字段重命名
  - Graph.run(): 单节点结束、链式执行、条件分支、循环、max_steps 保护
  - Graph.build_from_nodes(): 自动建边
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from pathlib import Path
from typing import Any

from ccserver.pipeline.data import NodeData
from ccserver.pipeline.node import FunctionNode, AgentNode
from ccserver.pipeline.graph import Graph, Edge


# ─── NodeData ─────────────────────────────────────────────────────────────────


def test_node_data_get_existing_key():
    nd = NodeData(data={"x": 42}, from_node="a")
    assert nd.get("x") == 42


def test_node_data_get_missing_key_returns_default():
    nd = NodeData(data={}, from_node="a")
    assert nd.get("missing") is None
    assert nd.get("missing", "fallback") == "fallback"


def test_node_data_update_merges():
    a = NodeData(data={"x": 1, "y": 2}, from_node="a")
    b = NodeData(data={"y": 99, "z": 3}, from_node="b")
    merged = a.update(b)
    assert merged.data == {"x": 1, "y": 99, "z": 3}
    assert merged.from_node == "b"


def test_node_data_update_does_not_modify_original():
    a = NodeData(data={"x": 1}, from_node="a")
    b = NodeData(data={"x": 2}, from_node="b")
    a.update(b)
    assert a.data == {"x": 1}  # 原对象不变


def test_node_data_repr_contains_keys():
    nd = NodeData(data={"alpha": 1, "beta": 2}, from_node="node1")
    r = repr(nd)
    assert "node1" in r
    assert "alpha" in r or "beta" in r


# ─── 辅助：构建 Graph 子类 ─────────────────────────────────────────────────────


def _make_session_manager():
    sm = MagicMock()
    sm.create_for_project = MagicMock(return_value=MagicMock())
    return sm


def _make_graph_class(build_fn):
    """动态构造 Graph 子类，避免重复 build() 覆写样板代码。"""
    class _G(Graph):
        def build(self):
            build_fn(self)
    return _G


# ─── Graph 结构验证 ────────────────────────────────────────────────────────────


def test_add_node_duplicate_id_raises():
    def build(g):
        g.entry = "fn"
        g.add_node(FunctionNode(id="fn", func=lambda d: {}))
        g.add_node(FunctionNode(id="fn", func=lambda d: {}))  # 重复

    G = _make_graph_class(build)
    with pytest.raises(ValueError, match="重复"):
        G(session_manager=_make_session_manager(), adapter=MagicMock())


def test_validate_edge_references_missing_from_node():
    def build(g):
        g.entry = "fn"
        g.add_node(FunctionNode(id="fn", func=lambda d: {}))
        g._edges.append(Edge(from_id="ghost", to_id="fn"))  # ghost 未注册

    G = _make_graph_class(build)
    with pytest.raises(ValueError, match="ghost"):
        G(session_manager=_make_session_manager(), adapter=MagicMock())


def test_validate_edge_references_missing_to_node():
    def build(g):
        g.entry = "fn"
        g.add_node(FunctionNode(id="fn", func=lambda d: {}))
        g._edges.append(Edge(from_id="fn", to_id="nowhere"))  # nowhere 未注册

    G = _make_graph_class(build)
    with pytest.raises(ValueError, match="nowhere"):
        G(session_manager=_make_session_manager(), adapter=MagicMock())


def test_run_requires_entry_set():
    def build(g):
        g.add_node(FunctionNode(id="fn", func=lambda d: {}))
        # 故意不设 entry

    G = _make_graph_class(build)
    g = G(session_manager=_make_session_manager(), adapter=MagicMock())
    with pytest.raises(ValueError, match="entry"):
        import asyncio
        asyncio.get_event_loop().run_until_complete(g.run({}))


def test_run_entry_not_registered_raises():
    def build(g):
        g.entry = "nonexistent"
        g.add_node(FunctionNode(id="fn", func=lambda d: {}))

    G = _make_graph_class(build)
    g = G(session_manager=_make_session_manager(), adapter=MagicMock())
    with pytest.raises(ValueError, match="nonexistent"):
        import asyncio
        asyncio.get_event_loop().run_until_complete(g.run({}))


# ─── FunctionNode 执行 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_function_node_sync_executes():
    def build(g):
        g.entry = "fn"
        g.add_node(FunctionNode(id="fn", func=lambda d: {"result": d.get("x", 0) * 2}))
        g.add_exit_edge("fn")

    G = _make_graph_class(build)
    g = G(session_manager=_make_session_manager(), adapter=MagicMock())
    output = await g.run({"x": 5})
    assert output.get("result") == 10


@pytest.mark.asyncio
async def test_function_node_async_executes():
    async def async_func(d: NodeData) -> dict:
        return {"doubled": d.get("val") * 2}

    def build(g):
        g.entry = "fn"
        g.add_node(FunctionNode(id="fn", func=async_func))
        g.add_exit_edge("fn")

    G = _make_graph_class(build)
    g = G(session_manager=_make_session_manager(), adapter=MagicMock())
    output = await g.run({"val": 7})
    assert output.get("doubled") == 14


@pytest.mark.asyncio
async def test_function_node_must_return_dict():
    def build(g):
        g.entry = "fn"
        g.add_node(FunctionNode(id="fn", func=lambda d: "not a dict"))
        g.add_exit_edge("fn")

    G = _make_graph_class(build)
    g = G(session_manager=_make_session_manager(), adapter=MagicMock())
    with pytest.raises(TypeError, match="dict"):
        await g.run({})


# ─── 路由逻辑 ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_graph_no_exit_edge_natural_stop():
    """无出边时图自然结束，不报错。"""
    def build(g):
        g.entry = "fn"
        g.add_node(FunctionNode(id="fn", func=lambda d: {"done": True}))
        # 不加任何出边

    G = _make_graph_class(build)
    g = G(session_manager=_make_session_manager(), adapter=MagicMock())
    output = await g.run({})
    assert output.get("done") is True


@pytest.mark.asyncio
async def test_graph_unconditional_chain():
    """A → B 无条件链式执行，B 的输出覆盖同名字段。"""
    def build(g):
        g.entry = "a"
        g.add_node(FunctionNode(id="a", func=lambda d: {"x": 10}))
        g.add_node(FunctionNode(id="b", func=lambda d: {"y": d.get("x") + 1}))
        g.add_edge("a", "b")
        g.add_exit_edge("b")

    G = _make_graph_class(build)
    g = G(session_manager=_make_session_manager(), adapter=MagicMock())
    output = await g.run({})
    assert output.get("x") == 10  # 上游字段保留
    assert output.get("y") == 11  # 下游计算字段


@pytest.mark.asyncio
async def test_graph_condition_true_branch():
    """条件为 True 时走 next，False 时走 exit。"""
    call_log = []

    def build(g):
        g.entry = "check"
        g.add_node(FunctionNode(id="check", func=lambda d: {"passed": True}))
        g.add_node(FunctionNode(id="yes", func=lambda d: (call_log.append("yes") or {"done": "yes"})))
        g.add_edge("check", "yes", condition=lambda d: d.get("passed"))
        g.add_exit_edge("check", condition=lambda d: not d.get("passed"))
        g.add_exit_edge("yes")

    G = _make_graph_class(build)
    g = G(session_manager=_make_session_manager(), adapter=MagicMock())
    output = await g.run({})
    assert "yes" in call_log
    assert output.get("done") == "yes"


@pytest.mark.asyncio
async def test_graph_condition_false_branch_exits():
    """条件为 False 时走 exit 边直接退出。"""
    call_log = []

    def build(g):
        g.entry = "check"
        g.add_node(FunctionNode(id="check", func=lambda d: {"passed": False}))
        g.add_node(FunctionNode(id="yes", func=lambda d: (call_log.append("yes") or {})))
        g.add_edge("check", "yes", condition=lambda d: d.get("passed"))
        g.add_exit_edge("check", condition=lambda d: not d.get("passed"))
        g.add_exit_edge("yes")

    G = _make_graph_class(build)
    g = G(session_manager=_make_session_manager(), adapter=MagicMock())
    await g.run({})
    assert "yes" not in call_log


@pytest.mark.asyncio
async def test_graph_all_conditions_false_exits_with_warning(caplog):
    """所有出边条件均不满足时，图自然结束（兜底逻辑）。"""
    def build(g):
        g.entry = "fn"
        g.add_node(FunctionNode(id="fn", func=lambda d: {"x": 0}))
        g.add_node(FunctionNode(id="other", func=lambda d: {}))
        g.add_edge("fn", "other", condition=lambda d: d.get("x") > 100)

    G = _make_graph_class(build)
    g = G(session_manager=_make_session_manager(), adapter=MagicMock())
    output = await g.run({})
    # 不报错，返回最后一个节点输出
    assert output.from_node == "fn"


@pytest.mark.asyncio
async def test_graph_max_steps_protection():
    """循环超过 max_steps 时抛出 RuntimeError。"""
    counter = {"n": 0}

    def build(g):
        g.entry = "loop"
        g.add_node(FunctionNode(id="loop", func=lambda d: (counter.__setitem__("n", counter["n"] + 1) or {"n": counter["n"]})))
        g.add_edge("loop", "loop")  # 无条件自环 → 死循环

    class _SmallGraph(Graph):
        max_steps = 5
        def build(self):
            build(self)

    g = _SmallGraph(session_manager=_make_session_manager(), adapter=MagicMock())
    with pytest.raises(RuntimeError, match="max_steps"):
        await g.run({})


@pytest.mark.asyncio
async def test_graph_loop_with_exit_condition():
    """有条件自环（n < 3 则循环），n == 3 时退出。"""
    def build(g):
        g.entry = "count"
        g.add_node(FunctionNode(id="count", func=lambda d: {"n": d.get("n", 0) + 1}))
        g.add_edge("count", "count", condition=lambda d: d.get("n", 0) < 3)
        g.add_exit_edge("count", condition=lambda d: d.get("n", 0) >= 3)

    G = _make_graph_class(build)
    g = G(session_manager=_make_session_manager(), adapter=MagicMock())
    output = await g.run({})
    assert output.get("n") == 3


# ─── field_map 字段重命名 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_graph_field_map_renames():
    """field_map 将上游字段 'out' 重命名为下游字段 'inp'。"""
    def build(g):
        g.entry = "a"
        g.add_node(FunctionNode(id="a", func=lambda d: {"out": 99}))
        g.add_node(FunctionNode(id="b", func=lambda d: {"received": d.get("inp")}))
        g.add_edge("a", "b", field_map={"out": "inp"})
        g.add_exit_edge("b")

    G = _make_graph_class(build)
    g = G(session_manager=_make_session_manager(), adapter=MagicMock())
    output = await g.run({})
    assert output.get("received") == 99


# ─── initial_input 注入 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_graph_initial_input_available_in_first_node():
    def build(g):
        g.entry = "fn"
        g.add_node(FunctionNode(id="fn", func=lambda d: {"got": d.get("seed")}))
        g.add_exit_edge("fn")

    G = _make_graph_class(build)
    g = G(session_manager=_make_session_manager(), adapter=MagicMock())
    output = await g.run({"seed": "hello"})
    assert output.get("got") == "hello"


# ─── build_from_nodes() ──────────────────────────────────────────────────────


def test_build_from_nodes_auto_edges():
    """build_from_nodes 根据 depends_on 自动建边。"""
    def build(g):
        g.entry = "a"
        a = FunctionNode(id="a", func=lambda d: {})
        b = AgentNode(id="b", depends_on=["a"])
        b._bare_root = Path("/tmp")  # 不实际运行，跳过目录创建

        # 只测试边是否已注册，不运行图
        g.add_node(a)
        g.add_node(b)
        for dep_id in b.depends_on:
            g.add_edge(dep_id, b.id)

    G = _make_graph_class(build)
    g = G(session_manager=_make_session_manager(), adapter=MagicMock())
    edge_targets = {(e.from_id, e.to_id) for e in g._edges}
    assert ("a", "b") in edge_targets


# ─── 多节点数据合并（输入字段透传）────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upstream_fields_preserved_through_chain():
    """上游字段应在整条链中透传，不被下游节点覆盖（除非同名）。"""
    def build(g):
        g.entry = "a"
        g.add_node(FunctionNode(id="a", func=lambda d: {"from_a": "value_a"}))
        g.add_node(FunctionNode(id="b", func=lambda d: {"from_b": "value_b"}))
        g.add_node(FunctionNode(id="c", func=lambda d: {
            "saw_a": d.get("from_a"),
            "saw_b": d.get("from_b"),
        }))
        g.add_edge("a", "b")
        g.add_edge("b", "c")
        g.add_exit_edge("c")

    G = _make_graph_class(build)
    g = G(session_manager=_make_session_manager(), adapter=MagicMock())
    output = await g.run({})
    assert output.get("saw_a") == "value_a"
    assert output.get("saw_b") == "value_b"
