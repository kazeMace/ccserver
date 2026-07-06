"""DisclosureLedger 披露账本测试（架构文档 §14 动态可见性）。"""

from __future__ import annotations

from drama_engine.core.engine import State, Vocabulary
from drama_engine.core.visibility.disclosure_ledger import DisclosureLedger
from drama_engine.core.visibility.knowledge_firewall import KnowledgeFirewall


def _state_with_secret() -> State:
    """构造带秘密 role 的状态。"""
    state = State(Vocabulary(frozenset(), frozenset(), frozenset(), frozenset()))
    state.register_entity("GAME", {"players": ["Player_1", "Player_2"], "round": 2})
    state.register_entity("Player_1", {"alive": True, "role": "seer"})
    state.register_entity("Player_2", {"alive": True, "role": "werewolf"})
    return state


def test_record_and_facts_for() -> None:
    """record 后 facts_for 返回该 actor 的已披露事实。"""
    ledger = DisclosureLedger()
    ledger.record("Player_1", "GAME.last_inspection_result", {"target": "Player_2", "role": "werewolf"})
    facts = ledger.facts_for("Player_1")
    assert facts["GAME.last_inspection_result"]["role"] == "werewolf"
    # 未被披露的 actor 拿不到
    assert ledger.facts_for("Player_2") == {}


def test_latest_value_wins() -> None:
    """同一 fact_ref 多次披露时返回最新值。"""
    ledger = DisclosureLedger()
    ledger.record("Player_1", "GAME.x", "old")
    ledger.record("Player_1", "GAME.x", "new")
    assert ledger.facts_for("Player_1")["GAME.x"] == "new"


def test_snapshot_restore_roundtrip() -> None:
    """snapshot/restore 往返一致。"""
    ledger = DisclosureLedger()
    ledger.record("Player_1", "GAME.a", 1, at_beat=1)
    ledger.record("Player_2", "GAME.b", 2, at_beat=2)
    snap = ledger.snapshot()

    restored = DisclosureLedger()
    restored.restore(snap)
    assert restored.facts_for("Player_1") == {"GAME.a": 1}
    assert restored.facts_for("Player_2") == {"GAME.b": 2}


def test_restore_truncates_later_disclosures() -> None:
    """回滚语义：restore 到旧快照后，其后新增的披露被截断丢弃。"""
    ledger = DisclosureLedger()
    ledger.record("Player_1", "GAME.a", 1)
    snap_before = ledger.snapshot()
    # 快照之后再披露一条
    ledger.record("Player_1", "GAME.b", 2)
    assert "GAME.b" in ledger.facts_for("Player_1")
    # 回滚到快照：GAME.b 应消失
    ledger.restore(snap_before)
    facts = ledger.facts_for("Player_1")
    assert facts == {"GAME.a": 1}
    assert "GAME.b" not in facts


def test_firewall_merges_disclosed_facts() -> None:
    """firewall 投影把已披露事实并入受限视图的 disclosed 字段。"""
    firewall = KnowledgeFirewall(secret_attrs=("role",))
    disclosed = {"GAME.last_inspection_result": {"target": "Player_2", "role": "werewolf"}}
    ctx = firewall.project_context(
        _state_with_secret(),
        "agent:Player_1",
        "prompt",
        disclosed_facts=disclosed,
    )
    # 他人 role 仍被遮蔽（静态可见性）
    assert "role" not in ctx["others"]["Player_2"]
    # 但已披露的验人结果出现在 disclosed 中（动态可见性）
    assert ctx["disclosed"]["GAME.last_inspection_result"]["role"] == "werewolf"


def test_firewall_without_disclosed_facts_has_empty_disclosed() -> None:
    """未传 disclosed_facts 时 disclosed 为空 dict。"""
    firewall = KnowledgeFirewall(secret_attrs=("role",))
    ctx = firewall.project_context(_state_with_secret(), "agent:Player_1", "prompt")
    assert ctx["disclosed"] == {}
