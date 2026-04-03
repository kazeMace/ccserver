"""
pipeline — 有向有环图 Pipeline 模块。

导出：
    Graph         基类，用户通过继承定义具体 graph（支持有环 + 退出条件）
    Edge          边的数据类（通常不需要直接使用，由 add_edge/add_exit_edge 创建）
    AgentNode     LLM Agent 节点规格
    FunctionNode  Python 函数节点规格
    NodeData      节点间传输的数据容器

向后兼容：
    Pipeline      旧 DAG 基类别名，保留以免影响已有代码
"""

from .data import NodeData
from .node import AgentNode, FunctionNode, MCPToolNode
from .graph import Graph, Edge

# 向后兼容旧名字
Pipeline = Graph

__all__ = ["Graph", "Edge", "AgentNode", "FunctionNode", "MCPToolNode", "NodeData", "Pipeline"]
