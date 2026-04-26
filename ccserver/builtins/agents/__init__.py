"""
builtins/agents -- 内置 Agent 定义目录。

两种内置 Agent 定义方式：
  1. Markdown + frontmatter（已有）：位于本目录下的 *.md 文件
  2. Python AgentSpec（新增）：位于 specs/ 目录下的 Python 类

AgentRegistry 自动扫描 specs/ 包，发现 BaseAgentSpec 子类并注册为 AgentDef。
AgentLoader 合并 Markdown Agent 和 Python AgentSpec 到统一的 agents 字典中。

导出：
  BaseAgentSpec -- 抽象基类，所有内置 AgentSpec 继承此类
  AgentRegistry -- 内置 Agent 注册表（pkgutil 自动发现）
  AgentConfig   -- agents.json 配置管理
  discover_builtin_agents() -- 便捷函数
  agent_registry() / agent_config() -- 全局单例
  ExploreAgentSpec / PlanAgentSpec / VerificationAgentSpec /
  CodeGuideAgentSpec / StatusLineAgentSpec -- 内置 Agent 定义
"""

from .base import BaseAgentSpec
from .registry import (
    AgentRegistry,
    agent_registry,
    discover_builtin_agents,
    discover_all_agents,
)
from .config import AgentConfig, agent_config
from .specs import (
    ExploreAgentSpec,
    PlanAgentSpec,
    VerificationAgentSpec,
    CodeGuideAgentSpec,
    StatusLineAgentSpec,
)

__all__ = [
    # 基类
    "BaseAgentSpec",
    # 注册表与配置
    "AgentRegistry",
    "AgentConfig",
    "agent_registry",
    "agent_config",
    "discover_builtin_agents",
    "discover_all_agents",
    # 内置 AgentSpec
    "ExploreAgentSpec",
    "PlanAgentSpec",
    "VerificationAgentSpec",
    "CodeGuideAgentSpec",
    "StatusLineAgentSpec",
]
