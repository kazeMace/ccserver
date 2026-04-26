"""
specs/status_line.py -- StatusLineAgentSpec 内置状态栏配置 Agent。

定位：帮助用户配置 Claude Code 状态栏。
对应 Claude Code：statuslineSetup.ts

职责：
  1. 读取 ~/.zshrc 等 Shell 配置
  2. 用正则提取 PS1 值
  3. 转换转义序列（\\u -> $(whoami) 等）
  4. 保留 ANSI 颜色
  5. 更新 ~/.claude/settings.json

特点：
  - Sonnet 模型：复杂编辑任务
  - Read + Edit：只读写配置文件
  - 橙色标记（color="orange"）
"""

from ..base import BaseAgentSpec


class StatusLineAgentSpec(BaseAgentSpec):
    """
    Claude Code 状态栏配置 Agent。

    定位：帮助配置 Claude Code 状态栏（编辑配置）。
    使用 Sonnet 模型处理复杂的配置编辑任务。
    """

    # -- 标识 --
    name = "StatusLine"
    description = "帮助配置 Claude Code 状态栏（编辑配置）"
    color = "orange"

    # -- 模型 --
    model_hint = "sonnet"

    # -- 工具控制 --
    # 只允许读写配置文件
    tools = [
        "Read",
        "Edit",
    ]

    # 禁止写文件、执行命令、询问用户
    disallowed_tools = [
        "Write",
        "Bash",
        "AskUserQuestion",
    ]

    # -- 运行限制 --
    round_limit = 30
    output_mode = "final_only"
