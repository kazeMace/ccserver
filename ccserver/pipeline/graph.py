"""
graph — Graph：有向有环图 + 状态机执行引擎。

与旧版 Pipeline（DAG）的核心区别：
  - 允许图中存在环（只要环上有退出条件就不会死循环）
  - 执行模型是状态机，而不是拓扑排序一次性跑完
  - 每条边可以带 condition，满足条件才走这条边
  - add_exit_edge() 表示"满足条件时结束整个图"
  - AgentNode.keep_session=True 时，同一节点跨 run() 调用复用 Session

用法示例：

    class QualityLoopGraph(Graph):
        def build(self):
            self.entry = "chat"

            self.add_node(AgentNode(id="chat", agent_dir=Path("playground/roleplay_agent"),
                                    prompt="...", keep_session=True))
            self.add_node(AgentNode(id="qc", agent_dir=Path("playground/roleplay_agent"),
                                    system_file=".ccserver/agents/quality-check.md",
                                    prompt="response={output}\\nhistory={history}",
                                    output_key="qc_json"))
            self.add_node(FunctionNode(id="parse_qc", func=parse_qc_result))

            self.add_edge("chat", "qc")
            self.add_edge("qc", "parse_qc")
            # 不通过 → 重试 chat（循环！）
            self.add_edge("parse_qc", "chat",
                          condition=lambda d: not d.get("passed"))
            # 通过 → 结束
            self.add_exit_edge("parse_qc",
                               condition=lambda d: d.get("passed"))
"""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from ..config import MODEL
from ..model import ModelAdapter, get_default_adapter
from ..factory import AgentFactory
from ..session import Session, SessionManager
from ..core.emitter import BaseEmitter
from ..mcp.manager import MCPManager
from .data import NodeData
from .node import AgentNode, FunctionNode, MCPToolNode


# ── Edge（有向边，带条件）─────────────────────────────────────────────────────


@dataclass
class Edge:
    """
    有向边。

    - from_id    起始节点 id
    - to_id      目标节点 id；None 表示退出整个图
    - condition  Callable[[NodeData], bool]；None = 无条件（始终满足）
    - field_map  字段重命名 {"上游字段": "下游字段"}；None = 原样传递
    """

    from_id: str
    to_id: str | None
    condition: Callable[[NodeData], bool] | None = None
    field_map: dict[str, str] | None = None


# ── Graph（有向有环图 + 状态机）────────────────────────────────────────────────


class Graph:
    """
    有向有环图执行引擎。

    子类通过重写 build() 定义节点和边，通过 run() 执行一次完整遍历。
    遍历从 self.entry 节点开始，沿满足条件的边推进，直到遇到 exit 边或无出边。
    """

    # 子类可覆盖的默认值；None 表示不限制步数
    max_steps: int | None = 100

    def __init__(
        self,
        session_manager: SessionManager,
        model: str = MODEL,
        adapter: ModelAdapter | None = None,
        mcp: MCPManager | None = None,
    ):
        self._session_manager = session_manager
        self._model = model
        self._adapter = adapter or get_default_adapter()
        self._mcp: MCPManager | None = mcp

        self._nodes: dict[str, AgentNode | FunctionNode | MCPToolNode] = {}
        self._edges: list[Edge] = []

        # keep_session=True 的节点，Session 在整个 Graph 实例生命周期内复用
        self._node_sessions: dict[str, Session] = {}

        # 入口节点 id，子类在 build() 中必须设置
        self.entry: str = ""

        self.build()
        self._validate()

    # ── 子类调用的构建 API ─────────────────────────────────────────────────────

    def add_node(self, node: AgentNode | FunctionNode) -> None:
        """注册一个节点。节点 id 在 Graph 内必须唯一。"""
        if node.id in self._nodes:
            raise ValueError(f"节点 id 重复: {node.id!r}")
        self._nodes[node.id] = node

    def add_edge(
        self,
        from_id: str,
        to_id: str,
        condition: Callable[[NodeData], bool] | None = None,
        field_map: dict[str, str] | None = None,
    ) -> None:
        """
        添加有向边 from_id → to_id。

        condition: 满足时才走这条边。None = 无条件。
        field_map: 字段重命名 {"上游字段": "下游字段"}。None = 原样传递。
        """
        self._edges.append(Edge(from_id=from_id, to_id=to_id,
                                condition=condition, field_map=field_map))

    def add_exit_edge(
        self,
        from_id: str,
        condition: Callable[[NodeData], bool] | None = None,
    ) -> None:
        """
        添加退出边：满足 condition 时结束整个图，返回当前节点的输出。

        condition 为 None 表示无条件退出（该节点执行完就结束）。
        """
        self._edges.append(Edge(from_id=from_id, to_id=None, condition=condition))

    def build_from_nodes(self, *nodes: AgentNode | FunctionNode) -> None:
        """
        批量注册节点，并根据 AgentNode.depends_on 自动生成无条件有向边。
        仅适用于 DAG 结构（无环），有环图请手动调用 add_edge。
        """
        for node in nodes:
            self.add_node(node)
        for node in nodes:
            if isinstance(node, AgentNode):
                for dep_id in node.depends_on:
                    self.add_edge(dep_id, node.id)

    # ── 子类重写 ──────────────────────────────────────────────────────────────

    def build(self) -> None:
        """子类在此方法中调用 add_node / add_edge / add_exit_edge 定义图结构。"""
        raise NotImplementedError("子类必须实现 build() 方法")

    # ── 执行接口 ──────────────────────────────────────────────────────────────

    async def run(
        self,
        initial_input: dict[str, Any],
        emitter: BaseEmitter | None = None,
    ) -> NodeData:
        """
        从 self.entry 开始，状态机式执行图，直到退出。

        initial_input: 注入入口节点的初始数据。
        emitter:       可选的事件发射器，传递给 AgentNode。
        返回最终输出节点的 NodeData。
        """
        if not self.entry:
            raise ValueError("Graph.entry 未设置，请在 build() 中指定入口节点 id")
        if self.entry not in self._nodes:
            raise ValueError(f"入口节点 {self.entry!r} 未注册")

        if emitter is None:
            emitter = _NullEmitter()

        current_id = self.entry
        current_data = NodeData(data=dict(initial_input), from_node="__input__")
        step = 0

        while True:
            node = self._nodes[current_id]
            logger.debug("Graph step={} node={} type={}", step, current_id, type(node).__name__)

            output = await self._run_node(node, current_data, emitter)

            # 将本节点输出合并回当前数据：输入字段保留，输出字段覆盖同名输入字段
            # 这样后续节点可以直接访问所有历史字段，无需手动透传
            merged = {**current_data.data, **output.data}
            output = NodeData(data=merged, from_node=current_id)

            # 找满足条件的第一条出边
            next_id, mapped_data = self._route(current_id, output)

            if next_id is None:
                # exit 边 或 无出边 → 结束
                logger.info("Graph finished at node={} step={}", current_id, step)
                return output

            step += 1
            if self.max_steps is not None and step >= self.max_steps:
                raise RuntimeError(
                    f"Graph 超过 max_steps={self.max_steps}，可能存在死循环。"
                    f"最后停在节点: {current_id!r}"
                )

            current_id = next_id
            current_data = mapped_data

    # ── 内部：路由 ────────────────────────────────────────────────────────────

    def _route(
        self,
        from_id: str,
        output: NodeData,
    ) -> tuple[str | None, NodeData]:
        """
        从 from_id 出发，按出边顺序找第一条满足条件的边。

        返回 (next_node_id, mapped_data)。
        next_node_id 为 None 表示退出（exit 边或无出边）。
        """
        out_edges = [e for e in self._edges if e.from_id == from_id]

        if not out_edges:
            # 无出边：自然结束
            return None, output

        for edge in out_edges:
            if edge.condition is None or edge.condition(output):
                if edge.to_id is None:
                    # exit 边
                    return None, output
                mapped = self._apply_field_map(output, edge.field_map)
                return edge.to_id, mapped

        # 所有出边条件均不满足：自然结束（兜底）
        logger.warning("Graph: node={} 所有出边条件均不满足，提前退出", from_id)
        return None, output

    def _apply_field_map(
        self,
        data: NodeData,
        field_map: dict[str, str] | None,
    ) -> NodeData:
        """按 field_map 对字段重命名，返回新 NodeData。"""
        if not field_map:
            return NodeData(data=dict(data.data), from_node=data.from_node)
        renamed: dict[str, Any] = {}
        for k, v in data.data.items():
            renamed[field_map.get(k, k)] = v
        return NodeData(data=renamed, from_node=data.from_node)

    # ── 内部：验证 ────────────────────────────────────────────────────────────

    def _validate(self) -> None:
        """检查边引用的节点均已注册。有环是允许的，不做环检测。"""
        for edge in self._edges:
            if edge.from_id not in self._nodes:
                raise ValueError(f"边引用了未注册的节点: {edge.from_id!r}")
            if edge.to_id is not None and edge.to_id not in self._nodes:
                raise ValueError(f"边引用了未注册的节点: {edge.to_id!r}")

    # ── 内部：Session 管理 ────────────────────────────────────────────────────

    def _get_session(self, node: AgentNode) -> Session:
        """
        获取节点对应的 Session。

        keep_session=True：在 Graph 实例生命周期内复用同一 Session（跨 run() 调用）。
        keep_session=False：每次调用都创建新 Session（用完即弃）。
        """
        if node.keep_session:
            if node.id not in self._node_sessions:
                project_root = _resolve_project_root(node)
                self._node_sessions[node.id] = self._session_manager.create_for_project(
                    project_root=project_root,
                )
            return self._node_sessions[node.id]
        else:
            project_root = _resolve_project_root(node)
            return self._session_manager.create_for_project(project_root=project_root)

    # ── 内部：运行单个节点 ────────────────────────────────────────────────────

    async def _run_node(
        self,
        node: AgentNode | FunctionNode | MCPToolNode,
        node_input: NodeData,
        emitter: BaseEmitter,
    ) -> NodeData:
        if isinstance(node, AgentNode):
            return await self._run_agent_node(node, node_input, emitter)
        elif isinstance(node, FunctionNode):
            return await self._run_function_node(node, node_input)
        elif isinstance(node, MCPToolNode):
            return await self._run_mcp_tool_node(node, node_input)
        else:
            raise TypeError(f"未知节点类型: {type(node)}")

    async def _run_agent_node(
        self,
        node: AgentNode,
        node_input: NodeData,
        emitter: BaseEmitter,
    ) -> NodeData:
        """
        为节点获取 Session，创建 Agent，执行并返回输出。

        Session 的 project_root 设为 node.agent_dir（若有），
        从而自动加载该目录下的 .ccserver/（子 agent、MCP、hooks 等）。
        """
        session = self._get_session(node)

        if node.prompt:
            try:
                prompt = node.prompt.format(**node_input.data)
            except KeyError as e:
                raise ValueError(
                    f"节点 {node.id!r} 的 prompt 引用了不存在的字段: {e}"
                ) from e
        else:
            # prompt 为空：把上游数据原样传给 agent，每个字段单独一行
            prompt = "\n".join(f"{k}: {v}" for k, v in node_input.data.items())

        system_text, append_system = _resolve_system(node, node_input)
        model = node.model or self._model

        agent = AgentFactory.create_root(
            name=node.id,
            session=session,
            emitter=emitter,
            model=model,
            adapter=node.adapter or self._adapter,
            system=system_text,
            append_system=append_system,
            **node.agent_config,
        )

        # pipeline 节点内的 agent 不允许再创建子代理（Task 工具）
        agent.tools.pop("Task", None)
        agent._schemas = [t.to_schema() for t in agent.tools.values()]

        final_text = await agent.run(prompt)
        return NodeData(data={node.output_key: final_text}, from_node=node.id)

    async def _run_function_node(
        self,
        node: FunctionNode,
        node_input: NodeData,
    ) -> NodeData:
        """运行 FunctionNode，支持同步和异步函数。"""
        if inspect.iscoroutinefunction(node.func):
            result = await node.func(node_input)
        else:
            result = node.func(node_input)

        if not isinstance(result, dict):
            raise TypeError(
                f"FunctionNode {node.id!r} 的 func 必须返回 dict，"
                f"实际返回: {type(result)}"
            )
        return NodeData(data=result, from_node=node.id)

    async def _run_mcp_tool_node(
        self,
        node: MCPToolNode,
        node_input: NodeData,
    ) -> NodeData:
        """
        直接调用 MCP 工具，不经过 LLM。

        args_map 中 value 含 {key} 占位符时从 NodeData 动态取值，否则作为固定字符串传入。
        """
        if self._mcp is None:
            raise RuntimeError(
                f"节点 {node.id!r} 是 MCPToolNode，但 Graph 未配置 mcp 参数"
            )

        client = self._mcp.get_client(node.server)
        if client is None:
            raise RuntimeError(
                f"节点 {node.id!r} 引用的 MCP server {node.server!r} 未找到或未连接"
            )

        # 解析 args_map：支持 {key} 占位符
        tool_args: dict[str, Any] = {}
        for param_name, value_expr in node.args_map.items():
            if "{" in value_expr and "}" in value_expr:
                try:
                    tool_args[param_name] = value_expr.format(**node_input.data)
                except KeyError as e:
                    raise ValueError(
                        f"节点 {node.id!r} 的 args_map[{param_name!r}] 引用了不存在的字段: {e}"
                    ) from e
            else:
                tool_args[param_name] = value_expr

        logger.debug("MCPToolNode | id={} server={} tool={} args={}", node.id, node.server, node.tool, tool_args)
        result = await client.call(node.tool, tool_args)
        return NodeData(data={node.output_key: result}, from_node=node.id)


# ── 工具函数 ──────────────────────────────────────────────────────────────────


def _resolve_project_root(node: AgentNode) -> Path:
    """
    确定 AgentNode 的 project_root。

    有 agent_dir → 用那个目录（自动加载其 .ccserver/、MCP 等）。
    无 agent_dir → 裸提示词模式，使用节点初始化时创建的隔离空目录
                  （node._bare_root，每个节点实例独立，不含 .ccserver/）。
    """
    if node.agent_dir is not None:
        p = Path(node.agent_dir).resolve()
        if not p.is_dir():
            raise ValueError(
                f"节点 {node.id!r} 的 agent_dir 不存在或不是目录: {p}"
            )
        return p

    return node._bare_root


def _resolve_system(node: AgentNode, node_input: NodeData) -> tuple[str | None, bool]:
    """
    解析节点的 system prompt，返回 (system_text, append_system)。

    优先级：
    1. system_file 存在（需配合 agent_dir）→ 读文件，append_system=False（文件即完整 system）
       - system_file 显式指定 → 用该文件
       - 未指定 → 按顺序查找：<dir_name>.md → instruct.md → 目录内第一个 *.md
    2. node.system 有值 → 格式化占位符后使用，append_system 由 node.append_system 决定
    3. 都没有 → (None, False)，AgentFactory 使用默认 system
    """
    # ── 优先：从 agent_dir 读取 system 文件 ──────────────────────────────────
    if node.agent_dir is not None:
        agent_dir = Path(node.agent_dir).resolve()

        if node.system_file:
            target = agent_dir / node.system_file
            if not target.exists():
                raise ValueError(
                    f"节点 {node.id!r} 的 system_file 不存在: {target}"
                )
            return target.read_text(encoding="utf-8"), False

        # 自动查找：<dir_name>.md → instruct.md → 第一个 *.md
        dir_name = agent_dir.name
        for candidate in [agent_dir / f"{dir_name}.md", agent_dir / "instruct.md"]:
            if candidate.exists():
                return candidate.read_text(encoding="utf-8"), False

        md_files = sorted(agent_dir.glob("*.md"))
        if md_files:
            return md_files[0].read_text(encoding="utf-8"), False

        logger.warning(
            "节点 {!r} 的 agent_dir={} 内未找到任何 .md 文件，system prompt 将使用默认值",
            node.id, agent_dir,
        )

    # ── 次选：内联 system（支持占位符）──────────────────────────────────────
    if node.system:
        try:
            system_text = node.system.format(**node_input.data)
        except KeyError as e:
            raise ValueError(
                f"节点 {node.id!r} 的 system 引用了不存在的字段: {e}"
            ) from e
        return system_text, node.append_system

    return None, False


# ── NullEmitter ────────────────────────────────────────────────────────────────


class _NullEmitter(BaseEmitter):
    """丢弃所有事件的空 emitter，在 Graph 无 emitter 时使用。"""

    async def emit(self, event: dict) -> None:
        pass
