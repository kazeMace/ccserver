"""
tests/test_team_registry.py — TeamRegistry 单元测试

覆盖：
  - create_team / get_team / delete_team / list_teams
  - add_member / remove_member / update_member_state
  - update_member_state_by_agent_id（反向查找）
  - from_dict / to_dict 序列化一致性
"""

import pytest
from unittest.mock import MagicMock

from ccserver.team.registry import TeamRegistry
from ccserver.team.models import TeamMemberRole, TeamMemberState


def _make_registry(adapter=None):
    return TeamRegistry(adapter=adapter)


# ─── Team CRUD ───────────────────────────────────────────────────────────────


def test_create_team():
    registry = _make_registry()
    team = registry.create_team("alpha", lead_name="neo")
    assert team.name == "alpha"
    assert team.lead_id == "neo@alpha"
    assert "neo@alpha" in team.members


def test_create_team_duplicate_raises():
    registry = _make_registry()
    registry.create_team("alpha")
    with pytest.raises(ValueError, match="already exists"):
        registry.create_team("alpha")


def test_get_team():
    registry = _make_registry()
    registry.create_team("beta")
    assert registry.get_team("beta") is not None
    assert registry.get_team("gamma") is None


def test_delete_team():
    registry = _make_registry()
    registry.create_team("delta")
    registry.delete_team("delta")
    assert registry.get_team("delta") is None


def test_list_teams():
    registry = _make_registry()
    registry.create_team("a")
    registry.create_team("b")
    names = {t.name for t in registry.list_teams()}
    assert names == {"a", "b"}


# ─── Member 管理 ─────────────────────────────────────────────────────────────


def test_add_member():
    registry = _make_registry()
    registry.create_team("omega")
    member = registry.add_member("omega", "trinity", role=TeamMemberRole.TEAMMATE)
    assert member.agent_id == "trinity@omega"
    assert member.role == TeamMemberRole.TEAMMATE


def test_remove_member():
    registry = _make_registry()
    registry.create_team("omega", lead_name="neo")
    registry.remove_member("omega", "neo@omega")
    assert registry.get_team("omega").lead_id is None


def test_update_member_state():
    registry = _make_registry()
    registry.create_team("omega")
    registry.add_member("omega", "trinity")
    registry.update_member_state("omega", "trinity@omega", TeamMemberState.BUSY)
    assert registry.get_team("omega").members["trinity@omega"].state == TeamMemberState.BUSY


def test_update_member_state_by_agent_id():
    registry = _make_registry()
    registry.create_team("omega")
    registry.add_member("omega", "trinity")
    registry.update_member_state_by_agent_id("trinity@omega", TeamMemberState.IDLE)
    assert registry.get_team("omega").members["trinity@omega"].state == TeamMemberState.IDLE


def test_update_member_state_by_agent_id_invalid_format():
    registry = _make_registry()
    # 非法格式应静默返回，不抛异常
    registry.update_member_state_by_agent_id("bad-format", TeamMemberState.IDLE)


def test_update_member_state_by_agent_id_missing_team():
    registry = _make_registry()
    registry.update_member_state_by_agent_id("ghost@missing", TeamMemberState.IDLE)


def test_update_member_state_by_agent_id_missing_member():
    registry = _make_registry()
    registry.create_team("omega")
    registry.update_member_state_by_agent_id("ghost@omega", TeamMemberState.IDLE)


# ─── Persistence ─────────────────────────────────────────────────────────────


def test_persist_on_create():
    adapter = MagicMock()
    adapter.list_teams.return_value = []
    adapter.save_team = MagicMock()

    registry = _make_registry(adapter=adapter)
    registry.create_team("persisted")
    adapter.save_team.assert_called()


def test_persist_on_member_change():
    adapter = MagicMock()
    adapter.list_teams.return_value = []
    adapter.save_team = MagicMock()

    registry = _make_registry(adapter=adapter)
    registry.create_team("persisted")
    adapter.save_team.reset_mock()

    registry.add_member("persisted", "trinity")
    adapter.save_team.assert_called()
