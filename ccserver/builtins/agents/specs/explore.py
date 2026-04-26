"""
specs/explore.py -- ExploreAgentSpec 内置探索 Agent。

定位：快速只读搜索，适合代码库探索、文件查找、模式分析。
对应 Claude Code：exploreAgent.ts

特点：
  - 严格只读：禁止所有写操作工具
  - Haiku 模型：低成本、快速响应
  - 不加载 CLAUDE.md：节省 token
  - 高并发搜索：鼓励并行工具调用
"""

from ..base import BaseAgentSpec


class ExploreAgentSpec(BaseAgentSpec):
    """
    快速只读探索 Agent。

    定位：代码库探索、文件查找、模式搜索。
    使用 Haiku 模型以降低成本，严格只读以确保安全。
    """

    # -- 标识 --
    name = "Explore"
    description = "代码库探索、文件查找、模式搜索（只读，快速）"

    # -- 模型 --
    model_hint = "haiku"
    omit_claude_md = True

    # -- 工具控制 --
    # 只读工具集
    tools = [
        "Read",
        "Glob",
        "Grep",
        "WebSearch",
        "WebFetch",
    ]

    # 禁止所有写操作
    disallowed_tools = [
        "Write",
        "Edit",
        "Bash",
    ]

    # -- 运行限制 --
    # 限制轮次（快速任务）
    round_limit = 20

    # 输出模式：只返回最终结果，不流式中间过程
    output_mode = "final_only"
