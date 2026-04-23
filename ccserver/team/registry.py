"""
team.registry — TeamRegistry 实现。

提供团队的内存缓存 + StorageAdapter 持久化能力。
兼容同步（file / sqlite）与异步（mongo）存储后端，
通过 _maybe_await 自动桥接。
"""

import asyncio
import inspect
from typing import Any

from loguru import logger

from ccserver.storage.base import StorageAdapter
from .models import Team, TeamMember, TeamMemberRole, TeamMemberState
from .helpers import format_agent_id


class TeamRegistry:
    """
    团队注册表。

    维护所有已加载团队的内存字典，并在变更时自动同步到 StorageAdapter。
    启动时从 StorageAdapter 全量加载团队列表。
    """

    def __init__(self, adapter: StorageAdapter | None = None):
        self._adapter = adapter
        self._teams: dict[str, Team] = {}
        if self._adapter is not None:
            self._load_from_storage()

    @staticmethod
    def _maybe_await(coro_or_result: Any) -> Any:
        """
        兼容同步与异步 adapter。

        如果返回值是协程对象（async def 的返回值），
        则启动事件循环并运行至完成；否则直接返回原值。
        """
        if inspect.isawaitable(coro_or_result):
            try:
                loop = asyncio.get_running_loop()
                # 若已有运行中的事件循环，使用线程池桥接避免嵌套错误
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, coro_or_result)
                    return future.result()
            except RuntimeError:
                # 无运行中的事件循环，可直接 asyncio.run
                return asyncio.run(coro_or_result)
        return coro_or_result

    # ── 加载与持久化 ───────────────────────────────────────────────────────────

    def _load_from_storage(self) -> None:
        """从 StorageAdapter 加载所有团队数据到内存。"""
        assert self._adapter is not None
        teams_data = self._maybe_await(self._adapter.list_teams())
        for data in teams_data:
            team = Team.from_dict(data)
            self._teams[team.name] = team
        logger.debug("TeamRegistry loaded | teams={}", len(self._teams))

    def _persist_team(self, team: Team) -> None:
        """将团队数据持久化到 StorageAdapter。"""
        if self._adapter is None:
            return
        self._maybe_await(self._adapter.save_team(team.to_dict()))

    def _delete_team_from_storage(self, team_name: str) -> None:
        """从 StorageAdapter 删除团队数据。"""
        if self._adapter is None:
            return
        self._maybe_await(self._adapter.delete_team(team_name))

    # ── 公共接口 ───────────────────────────────────────────────────────────────

    def create_team(
        self,
        name: str,
        lead_name: str | None = None,
        allowed_paths: list[str] | None = None,
    ) -> Team:
        """
        创建一个新团队。

        Args:
            name:          团队名称，全局唯一
            lead_name:     队长名称（可选），会自动创建为 LEAD 成员
            allowed_paths: 团队共享允许路径列表（可选）

        Returns:
            新创建的 Team 对象

        Raises:
            ValueError: 当名称为空或团队已存在时
        """
        if not name or not name.strip():
            raise ValueError("team name is required")
        name = name.strip()
        if name in self._teams:
            raise ValueError(f"Team '{name}' already exists")

        team = Team(name=name, allowed_paths=allowed_paths or [])
        if lead_name:
            lead_id = format_agent_id(lead_name, name)
            team.lead_id = lead_id
            team.members[lead_id] = TeamMember(
                agent_id=lead_id,
                name=lead_name,
                role=TeamMemberRole.LEAD,
            )

        self._teams[name] = team
        self._persist_team(team)
        logger.info("TeamRegistry: created | name={} lead={}", name, lead_name)
        return team

    def get_team(self, name: str) -> Team | None:
        """按名称获取团队，不存在返回 None。"""
        return self._teams.get(name)

    def delete_team(self, name: str) -> None:
        """删除指定团队（内存 + 持久化）。"""
        if name not in self._teams:
            raise ValueError(f"Team '{name}' not found")
        del self._teams[name]
        self._delete_team_from_storage(name)
        logger.info("TeamRegistry: deleted | name={}", name)

    def list_teams(self) -> list[Team]:
        """返回所有已加载的团队列表。"""
        return list(self._teams.values())

    def add_member(
        self,
        team_name: str,
        name: str,
        role: TeamMemberRole = TeamMemberRole.TEAMMATE,
        color: str | None = None,
        metadata: dict | None = None,
    ) -> TeamMember:
        """
        向指定团队添加成员。

        Args:
            team_name: 目标团队名称
            name:      成员名称
            role:      成员角色，默认 TEAMMATE
            color:     UI 颜色（可选）
            metadata:  扩展字段（可选）

        Returns:
            新创建的 TeamMember 对象
        """
        team = self._teams.get(team_name)
        if team is None:
            raise ValueError(f"Team '{team_name}' not found")

        agent_id = format_agent_id(name, team_name)
        if agent_id in team.members:
            raise ValueError(f"Member '{agent_id}' already exists in team '{team_name}'")

        member = TeamMember(
            agent_id=agent_id,
            name=name,
            role=role,
            color=color,
            metadata=metadata or {},
        )
        team.members[agent_id] = member
        self._persist_team(team)
        logger.info(
            "TeamRegistry: add_member | team={} agent_id={} role={}",
            team_name,
            agent_id,
            role.value,
        )
        return member

    def remove_member(self, team_name: str, agent_id: str) -> None:
        """从团队中移除成员；若移除的是 Lead，则清空 lead_id。"""
        team = self._teams.get(team_name)
        if team is None:
            raise ValueError(f"Team '{team_name}' not found")
        if agent_id not in team.members:
            raise ValueError(f"Member '{agent_id}' not found in team '{team_name}'")

        del team.members[agent_id]
        if team.lead_id == agent_id:
            team.lead_id = None
        self._persist_team(team)
        logger.info("TeamRegistry: remove_member | team={} agent_id={}", team_name, agent_id)

    def update_member_state(self, team_name: str, agent_id: str, state: TeamMemberState) -> None:
        """更新团队成员的运行状态。"""
        team = self._teams.get(team_name)
        if team is None:
            raise ValueError(f"Team '{team_name}' not found")
        member = team.members.get(agent_id)
        if member is None:
            raise ValueError(f"Member '{agent_id}' not found in team '{team_name}'")

        member.state = state
        self._persist_team(team)
        logger.debug(
            "TeamRegistry: update_state | team={} agent_id={} state={}",
            team_name,
            agent_id,
            state.value,
        )

    def update_member_state_by_agent_id(self, agent_id: str, state: TeamMemberState) -> None:
        """
        通过 agent_id 反向查找团队并更新成员状态。

        agent_id 格式为 name@teamName，可直接解析出团队名。
        若找不到对应团队或成员，则静默返回（不抛异常），
        以便在后台协程中安全调用。
        """
        from .helpers import parse_agent_id
        try:
            name, team_name = parse_agent_id(agent_id)
        except ValueError:
            logger.warning("TeamRegistry: invalid agent_id format | agent_id={}", agent_id)
            return

        team = self._teams.get(team_name)
        if team is None:
            logger.debug("TeamRegistry: team not found | team={}", team_name)
            return

        member = team.members.get(agent_id)
        if member is None:
            logger.debug("TeamRegistry: member not found | agent_id={}", agent_id)
            return

        member.state = state
        self._persist_team(team)
        logger.debug(
            "TeamRegistry: update_state_by_agent_id | team={} agent_id={} state={}",
            team_name,
            agent_id,
            state.value,
        )
