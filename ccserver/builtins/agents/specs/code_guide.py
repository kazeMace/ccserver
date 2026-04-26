"""
specs/code_guide.py -- CodeGuideAgentSpec 内置指南 Agent。

定位：回答 Claude Code / SDK / API 相关问题。
对应 Claude Code：claudeCodeGuideAgent.ts

三大领域：
  1. Claude Code CLI：安装、配置、hooks、skills、MCP、快捷键
  2. Claude Agent SDK：构建自定义 Agent（Python/TypeScript）
  3. Claude API：Messages API、流式、工具调用

特点：
  - Haiku 模型：低成本问答
  - 工具自动批准：快速响应
  - 策略：先抓官方文档，再针对性抓取
"""

from ..base import BaseAgentSpec


class CodeGuideAgentSpec(BaseAgentSpec):
    """
    Claude Code 指南 Agent。

    定位：回答 Claude Code / SDK / API 使用问题。
    使用 Haiku 模型以降低成本，只读搜索 + 网页抓取。
    """

    # -- 标识 --
    name = "CodeGuide"
    description = "回答 Claude Code / SDK / API 使用问题（快速问答）"

    # -- 模型 --
    model_hint = "haiku"
    omit_claude_md = True
    auto_approve_tools = True

    # -- 工具控制 --
    # 只读工具（搜索 + 抓取网页）
    tools = [
        "WebSearch",
        "WebFetch",
        "Glob",
        "Grep",
    ]

    # 禁止所有写操作
    disallowed_tools = [
        "Write",
        "Edit",
        "Bash",
        "AskUserQuestion",
    ]

    # -- 运行限制 --
    round_limit = 20
    output_mode = "final_only"
