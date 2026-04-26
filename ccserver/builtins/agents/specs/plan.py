"""
specs/plan.py -- PlanAgentSpec 内置计划 Agent。

定位：软件架构设计、实现计划生成。
对应 Claude Code：planAgent.ts

特点：
  - 只读规划：不写代码，只生成计划
  - 继承 Explore 的工具集（新增 WebSearch/WebFetch）
  - 继承父模型（与根 Agent 相同）
  - 输出必须包含「关键文件列表」
"""

from ..base import BaseAgentSpec


class PlanAgentSpec(BaseAgentSpec):
    """
    软件架构计划 Agent。

    定位：软件架构设计、实现计划生成。
    只读规划，不执行命令，不写代码文件。
    """

    # -- 标识 --
    name = "Plan"
    description = "软件架构设计、实现计划生成（只读规划）"

    # -- 模型 --
    model_hint = "inherit"
    omit_claude_md = True

    # -- 工具控制 --
    # 允许读 + 写文件（写 plan.md）
    tools = [
        "Read",
        "Write",
        "Glob",
        "Grep",
        "WebSearch",
        "WebFetch",
    ]

    # 禁止执行命令（计划阶段不需要）
    disallowed_tools = [
        "Bash",
    ]

    # -- 运行限制 --
    round_limit = 50
    output_mode = "final_only"
