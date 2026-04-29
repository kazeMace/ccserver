"""
builtins/agents/specs -- 内置 AgentSpec 定义目录。

所有内置 AgentSpec 子类定义在此包中。
AgentRegistry.discover() 自动扫描本包，发现 BaseAgentSpec 子类。

包含 Agent：
  Explore      -- 快速只读搜索（haiku 模型）
  Plan         -- 软件架构规划
  Verification -- 验证测试（后台运行）
  CodeGuide    -- Claude Code 使用指南
  StatusLine   -- 状态栏配置
  ScreenAgent  -- 视觉感知与机器控制（截图 + VLM + 鼠标键盘）
"""

from .explore import ExploreAgentSpec
from .plan import PlanAgentSpec
from .verification import VerificationAgentSpec
from .code_guide import CodeGuideAgentSpec
from .status_line import StatusLineAgentSpec
from .screen_agent import ScreenAgentSpec

__all__ = [
    "ExploreAgentSpec",
    "PlanAgentSpec",
    "VerificationAgentSpec",
    "CodeGuideAgentSpec",
    "StatusLineAgentSpec",
    "ScreenAgentSpec",
]
