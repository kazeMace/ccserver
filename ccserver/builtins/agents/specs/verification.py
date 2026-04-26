"""
specs/verification.py -- VerificationAgentSpec 内置验证 Agent。

定位：验证实现正确性、运行测试、执行对抗性测试。
对应 Claude Code：verificationAgent.ts

特点：
  - 自动后台运行（auto_background=True）
  - 允许所有执行工具（Read/Write/Edit/Bash）
  - 强制对抗性测试要求
  - 标准化验证报告格式（PASS/FAIL/PARTIAL）
  - 红色标记（color="red"）
"""

from ..base import BaseAgentSpec


class VerificationAgentSpec(BaseAgentSpec):
    """
    验证 Agent。

    定位：验证实现正确性、运行测试、执行破坏性测试。
    允许写测试文件和执行命令，强制对抗性测试要求。
    """

    # -- 标识 --
    name = "Verification"
    description = "验证实现正确性、运行测试、执行破坏性测试（后台）"
    color = "red"

    # -- 模型 --
    model_hint = "inherit"
    omit_claude_md = True

    # -- 运行模式 --
    auto_background = True

    # -- 工具控制 --
    # 全工具集（执行验证需要写文件、跑命令）
    tools = [
        "Read",
        "Write",
        "Edit",
        "Glob",
        "Grep",
        "Bash",
        "WebSearch",
        "WebFetch",
        "TaskCreate",
        "TaskUpdate",
        "TaskGet",
        "TaskList",
    ]

    # -- 运行限制 --
    round_limit = 100
    output_mode = "final_only"
    auto_approve_tools = True
