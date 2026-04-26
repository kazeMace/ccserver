"""
team.helpers — Agent Team 相关的工具函数。

提供确定性 ID 的格式化、解析，以及角色判断等辅助功能。
"""


from .models import Team


def format_agent_id(name: str, team_name: str) -> str:
    """
    将 agent 名称和团队名称组合为确定性全局唯一 ID。

    格式：name@teamName
    示例：coder@security-team

    Args:
        name:      agent 的名称，不能为空
        team_name: 团队名称，不能为空

    Returns:
        格式化后的 agent_id 字符串

    Raises:
        ValueError: 当 name 或 team_name 为空时触发
    """
    if not name or not name.strip():
        raise ValueError("agent name must not be empty")
    if not team_name or not team_name.strip():
        raise ValueError("team name must not be empty")
    return f"{name}@{team_name}"


def parse_agent_id(agent_id: str) -> tuple[str | None, str | None]:
    """
    将 agent_id 解析为 (name, team_name)。

    Args:
        agent_id: 格式为 name@teamName 的字符串

    Returns:
        (name, team_name) 元组。空字符串返回 (None, None)。

    Raises:
        ValueError: 当 agent_id 非空但格式不正确（不包含 @）时抛出，
                    便于调用方区分"格式错误"和"正常空值"。
    """
    if not agent_id:
        return None, None
    if "@" not in agent_id:
        raise ValueError(
            f"invalid agent_id format: expected 'name@teamName', got '{agent_id}'"
        )
    name, team_name = agent_id.rsplit("@", 1)
    return name, team_name


def is_lead(agent_id: str, team: Team) -> bool:
    """
    判断指定 agent_id 是否为团队的队长（Lead）。

    Args:
        agent_id: 要判断的 agent_id
        team:     目标团队对象

    Returns:
        True 当 agent_id 等于 team.lead_id，否则 False
    """
    return team.lead_id == agent_id
