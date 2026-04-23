"""
team — Agent Team 基础抽象模块。

提供团队（Team）、成员（TeamMember）的数据结构，
以及 TeamRegistry（团队注册表）用于创建、查询、更新团队状态。
"""

from .models import Team, TeamMember, TeamMemberRole, TeamMemberState
from .helpers import format_agent_id, parse_agent_id, is_lead
from .registry import TeamRegistry
from .mailbox import TeamMailbox
from .poller import TeamMailboxPoller
from .dispatcher import TeamTaskDispatcher
from .permission_relay import TeamPermissionRelay
from .prompts import build_teammate_system_addendum
from .monitor import TeamHealthMonitor

__all__ = [
    "Team",
    "TeamMember",
    "TeamMemberRole",
    "TeamMemberState",
    "format_agent_id",
    "parse_agent_id",
    "is_lead",
    "TeamRegistry",
    "TeamMailbox",
    "TeamMailboxPoller",
    "TeamTaskDispatcher",
    "TeamPermissionRelay",
    "build_teammate_system_addendum",
    "TeamHealthMonitor",
]
