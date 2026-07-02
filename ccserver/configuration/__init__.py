"""
ccserver.configuration — 配置系统的单一真相源（取代旧 config.py + settings.py）。

三作用域（见 spec §3）：
  ProcessConfig  —— 进程级共享底座（infra + 默认 model + 密钥），解析一次。
  CcServerConfig —— SESSION 作用域完整配置；Agent 作用域在其上叠加覆盖。

对外入口：
  from ccserver.configuration import ProcessConfig, CcServerConfig
  from ccserver.configuration import resolve_session, resolve_agent
"""

from .schema import (
    CcServerConfig,
    ModelConfig,
    VlmConfig,
    AgentBehaviorConfig,
    PermissionConfig,
    ToolConfig,
    CompactionConfig,
    InfraConfig,
)
from .loader import ProcessConfig, resolve_session, resolve_agent, deep_merge, get_process_config

__all__ = [
    "CcServerConfig",
    "ModelConfig",
    "VlmConfig",
    "AgentBehaviorConfig",
    "PermissionConfig",
    "ToolConfig",
    "CompactionConfig",
    "InfraConfig",
    "ProcessConfig",
    "resolve_session",
    "resolve_agent",
    "deep_merge",
    "get_process_config",
]
