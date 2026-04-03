"""
data — NodeData：节点间传输的数据容器。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NodeData:
    """
    节点间传递的数据容器，本质上是一个带来源标记的 dict 包装。

    - data       实际数据，key → value
    - from_node  来自哪个节点（调试 / 日志用）
    """

    data: dict[str, Any] = field(default_factory=dict)
    from_node: str = ""

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def update(self, other: "NodeData") -> "NodeData":
        """合并另一个 NodeData，返回新对象（不修改 self）。"""
        merged = {**self.data, **other.data}
        return NodeData(data=merged, from_node=other.from_node or self.from_node)

    def __repr__(self) -> str:
        keys = list(self.data.keys())
        return f"<NodeData from={self.from_node!r} keys={keys}>"
