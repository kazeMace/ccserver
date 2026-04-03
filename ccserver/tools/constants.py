"""
工具集权限常量。

对应 Claude Code 的三层工具集设计：
  CHILD_DISALLOWED_TOOLS   → ALL_AGENT_DISALLOWED_TOOLS
  CHILD_DEFAULT_TOOLS      → ASYNC_AGENT_ALLOWED_TOOLS
  TEAMMATE_EXTRA_TOOLS     → IN_PROCESS_TEAMMATE_ALLOWED_TOOLS（增量部分）
"""

# 子代理永远不可见，无论任何配置（硬编码，不可被 agent_def 或 settings 覆盖）
#
# AskUserQuestion : 子代理无法与用户交互，API 层只有根代理绑定 emitter，
#                   调用只会静默返回空字符串，导致 LLM 幻觉
# Compact         : 子代理 persist=False，触发压缩无意义
# Agent           : 防止子代理派生孙代理（递归）
CHILD_DISALLOWED_TOOLS: frozenset[str] = frozenset({
    "AskUserQuestion",
    "Compact",
    "Agent",
})

# 无 agent_def 时子代理的默认工具白名单（最小权限原则）
# 对应 Claude Code ASYNC_AGENT_ALLOWED_TOOLS 的精神：
# 只包含任务执行必要的基础工具，不包含主线程专属工具
CHILD_DEFAULT_TOOLS: frozenset[str] = frozenset({
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash",
    "WebSearch",
    "WebFetch",
})

# agent_def 声明 is_teammate=true 时，在 CHILD_DEFAULT_TOOLS 基础上额外允许
# 对应 Claude Code IN_PROCESS_TEAMMATE_ALLOWED_TOOLS 的增量部分
# Teammate 需要管理会话级任务列表，普通子代理不需要
TEAMMATE_EXTRA_TOOLS: frozenset[str] = frozenset({
    "TaskCreate",
    "TaskUpdate",
    "TaskGet",
    "TaskList",
})
