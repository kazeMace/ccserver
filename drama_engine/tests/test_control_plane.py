"""ControlPlane 与 KnowledgeFirewall 测试（架构文档 §11/§14）。"""

from __future__ import annotations

from drama_engine.core.control_plane.plane import build_control_plane
from drama_engine.core.control_plane.roles import ControlProposal, ControlRole, ProposalVerdict
from drama_engine.core.engine import State, StateWriter, Vocabulary, SetAttr
from drama_engine.core.visibility.knowledge_firewall import KnowledgeFirewall


def _state_with_secret() -> State:
    """构造带秘密 role 的状态。"""
    state = State(Vocabulary(frozenset(), frozenset(), frozenset(), frozenset()))
    state.register_entity("GAME", {"players": ["Player_1", "Player_2"], "round": 1})
    state.register_entity("Player_1", {"alive": True, "role": "werewolf"})
    state.register_entity("Player_2", {"alive": True, "role": "seer"})
    return state


def test_control_plane_build_from_spec() -> None:
    """从 DSL 声明构建控制角色。"""
    plane = build_control_plane({
        "referee": {"type": "system"},
        "writer": {"type": "agent", "agent_id": "narrator"},
    })
    assert plane.has_role("referee")
    assert plane.has_role("writer")
    assert plane.get_role("writer").actor_type == "agent"
    assert plane.get_role("writer").config["agent_id"] == "narrator"


def test_proposal_pipeline_approves_and_applies() -> None:
    """提案通过裁定后应被 applier 应用。"""
    applied: list[ControlProposal] = []
    plane = build_control_plane(
        {"writer": {"type": "agent"}},
        applier=applied.append,
    )
    verdict = plane.submit_proposal(ControlProposal(
        role="writer", kind="announcement", payload={"text": "夜幕降临"},
    ))
    assert verdict.approved is True
    assert len(applied) == 1
    assert plane.proposals()[0]["applied"] is True


def test_proposal_rejected_is_not_applied() -> None:
    """未知提案类型应被拒绝且不应用。"""
    applied: list[ControlProposal] = []
    plane = build_control_plane({"director": {"type": "system"}}, applier=applied.append)
    verdict = plane.submit_proposal(ControlProposal(
        role="director", kind="unknown_kind", payload={"x": 1},
    ))
    assert verdict.approved is False
    assert applied == []


def test_custom_validator_can_reject() -> None:
    """注入的裁定函数可以否决提案（模拟 referee 校验）。"""
    def reject_all(_proposal: ControlProposal) -> ProposalVerdict:
        return ProposalVerdict(False, "referee 否决")

    applied: list[ControlProposal] = []
    plane = build_control_plane(
        {"writer": {"type": "agent"}},
        validator=reject_all,
        applier=applied.append,
    )
    verdict = plane.submit_proposal(ControlProposal(role="writer", kind="patch", payload={"type": "add_scene"}))
    assert verdict.approved is False
    assert applied == []


def test_firewall_hides_secrets_from_other_players() -> None:
    """玩家视角只见自己完整属性，他人的 role 被遮蔽。"""
    firewall = KnowledgeFirewall()
    ctx = firewall.project_context(_state_with_secret(), "player:Player_1", "prompt")
    assert ctx["self"]["role"] == "werewolf"       # 自己可见
    assert "role" not in ctx["others"]["Player_2"]  # 他人秘密被遮蔽
    assert ctx["others"]["Player_2"]["alive"] is True  # 公开属性可见


def test_firewall_gives_privileged_audience_full_state() -> None:
    """host / referee 授权拿到完整 state。"""
    firewall = KnowledgeFirewall()
    ctx = firewall.project_context(_state_with_secret(), "host", "referee")
    assert "state" in ctx
    assert ctx["state"]["Player_1"]["role"] == "werewolf"
