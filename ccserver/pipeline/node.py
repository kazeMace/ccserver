"""
node — AgentNode 与 FunctionNode 的规格声明。

这里只描述"节点是什么"，不包含执行逻辑。
执行逻辑在 graph.py 的 Graph._run_node() 中。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# 裸模式节点的临时目录统一放在这里，按实例 uuid 隔离
_BARE_ROOT_BASE = Path(__file__).parent / "_bare_roots"


@dataclass
class AgentNode:
    """
    由 LLM Agent 执行的节点。

    - id           节点唯一标识，也用于边的引用
    - prompt       用户消息内容，支持 {key} 占位符。
                   为空时，把上游 NodeData 的字段逐行拼成消息传给 agent。
    - agent_dir    playground 下的 agent 项目目录（如 Path("playground/roleplay_agent")）。
                   该目录即为这个 agent 的 project_root，框架从这里读取 .ccserver/、MCP 配置等。
                   为 None 时使用裸提示词模式，自动创建隔离的空 project root。
    - system_file  agent_dir 内 system prompt 的相对路径（如 "roleplay_instruct.md"
                   或 ".ccserver/agents/quality-check.md"）。
                   不填时按优先级自动查找：<dir_name>.md → instruct.md → 目录内第一个 *.md。
                   system_file 存在时忽略 system 字段。
    - system       内联 system prompt，支持 {key} 占位符（与 prompt 共享同一份 node_input）。
                   system_file 优先；都没有时 AgentFactory 使用默认 system。
    - append_system True=将 system 追加到 prompt lib 模板末尾；False=完全替换模板。
                   仅对内联 system 有效，system_file 始终使用 False（文件即完整 system）。
    - model        覆盖全局 MODEL；None = 使用全局配置
    - output_key   Agent 最终输出存入 NodeData 的字段名
    - keep_session True 时，同一节点在 Graph 生命周期内复用同一个 Session（跨 run() 调用）。
                   适合需要保留 agent 记忆的多轮对话场景。
    - depends_on   前置节点 id 列表（仅用于 build_from_nodes() 自动建边，与有环图执行无关）
    - adapter      覆盖 Graph 层的 ModelAdapter，用于节点使用不同的 API key 或 base_url。
                   为 None 时使用 Graph 初始化时传入的 adapter。
    - agent_config 覆盖 AgentFactory.create_root() 的其他参数，仅在通过 Graph 创建时生效。
                   可用 key：prompt_version、language、run_mode、round_limit。
                   示例：{"prompt_version": "cc_reverse:v2.1.81", "language": "English"}
    """

    id: str
    prompt: str = ""
    agent_dir: Path | None = None
    system_file: str | None = None
    system: str | None = None          # 支持 {key} 占位符
    append_system: bool = False        # True=追加到 prompt lib 末尾；False=替换
    model: str | None = None
    output_key: str = "output"
    keep_session: bool = False
    depends_on: list[str] = field(default_factory=list)
    agent_config: dict[str, Any] = field(default_factory=dict)
    adapter: Any = field(default=None, repr=False)  # 优先于 Graph 层的 adapter

    # 裸提示词模式下自动创建的隔离目录，外部无需传入
    _bare_root: Path = field(default=None, init=False, repr=False)

    def __post_init__(self):
        # 没有 agent_dir 时，为这个节点实例创建一个唯一的空目录
        # 不含任何 .ccserver/，Session 初始化后完全干净
        if self.agent_dir is None:
            self._bare_root = _BARE_ROOT_BASE / str(uuid.uuid4())
            self._bare_root.mkdir(parents=True, exist_ok=True)


@dataclass
class FunctionNode:
    """
    由普通 Python 函数执行的节点（同步或异步均可）。

    - id    节点唯一标识
    - func  接收 NodeData，返回 dict；可以是同步或 async 函数
    """

    id: str
    func: Callable[["NodeData"], dict[str, Any]]  # type: ignore[name-defined]


@dataclass
class MCPToolNode:
    """
    直接调用 MCP 工具的节点，不经过 LLM，适合确定性的工具调用。

    - id          节点唯一标识
    - server      .mcp.json 中的 server 名称（如 "web-search"、"weather"）
    - tool        工具函数名（如 "search_web"、"get_weather"）
    - args_map    从 NodeData 字段映射到工具参数的字典。
                  key = 工具参数名，value = NodeData 字段名或固定值字符串。
                  支持 {key} 占位符语法：value 中包含 {key} 时从 NodeData 动态取值，
                  否则直接作为固定字符串传入。
                  示例：{"query": "{current_query}", "max_results": "5"}
    - output_key  工具返回值存入 NodeData 的字段名
    """

    id: str
    server: str
    tool: str
    args_map: dict[str, str]
    output_key: str = "output"
