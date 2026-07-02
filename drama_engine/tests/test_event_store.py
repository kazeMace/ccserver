"""Tests for Drama Engine session event storage."""

from __future__ import annotations

from drama_engine.core.session.events import SessionEventStore


def test_event_store_assigns_unique_session_seq_for_trace_events() -> None:
    """tracer 自带 seq 时，服务端仍应分配不冲突的 session seq。"""
    store = SessionEventStore("session-1")

    store.append_public({"kind": "session_started"})
    store.append_host({"kind": "act", "seq": 1, "actor": "Player_1", "text": "发言 A"})
    store.append_host({"kind": "perceive", "seq": 2, "actor": "Player_2", "sender": "Player_1", "text": "发言 A"})

    host_events = store.host_backlog()
    seq_values = [event["seq"] for event in host_events]

    assert seq_values == [1, 2, 3]
    assert host_events[1]["trace_seq"] == 1
    assert host_events[2]["trace_seq"] == 2
    assert len(seq_values) == len(set(seq_values))


def test_host_backlog_keeps_live_event_order() -> None:
    """host 回放应按真实追加顺序混合 public 与 host-only 事件。"""
    store = SessionEventStore("session-1")

    store.append_public({"kind": "session_started"})
    store.append_host({"kind": "act", "actor": "Player_1", "text": "1 号发言"})
    store.append_public({"kind": "narration", "scope": "public", "text": "公开通报"})
    store.append_host({"kind": "perceive", "actor": "Player_2", "sender": "Player_1", "text": "1 号发言"})

    assert [event["kind"] for event in store.host_backlog()] == [
        "session_started",
        "act",
        "narration",
        "perceive",
    ]


def test_public_event_is_not_duplicated_for_host_subscriber() -> None:
    """public 事件已经会推给 host 订阅者，不需要 host 再存一份。"""
    store = SessionEventStore("session-1")
    subscriber = store.subscribe_host()

    store.append_public({"kind": "narration", "scope": "public", "text": "天亮了"})

    assert subscriber.queue.qsize() == 1
    assert len(store.host_backlog()) == 1
    assert store.host_backlog()[0]["text"] == "天亮了"

from drama_engine.core.engine import SetAttr, State, StateWriter, Vocabulary
from drama_engine.core.execution_models.fixed_flow import SocialDeductionGameRunner


def test_dashboard_masks_night_kill_until_death_report() -> None:
    """wolf/poison 夜间死亡必须等 GAME.night_deaths 记录后才展示出局。"""
    state = State(Vocabulary(frozenset(), frozenset(), frozenset(), frozenset()))
    state.register_entity("GAME", {})
    state.register_entity("Player_1", {})
    writer = StateWriter(state)
    writer.apply(SetAttr("GAME", "round", 1))
    writer.apply(SetAttr("Player_1", "alive", False))
    writer.apply(SetAttr("Player_1", "death_cause", "wolf"))
    writer.apply(SetAttr("Player_1", "death_round", 1))

    assert SocialDeductionGameRunner._visible_alive_for_dashboard(state, "Player_1", False) is True

    writer.apply(SetAttr("GAME", "night_deaths", ["Player_1"]))

    assert SocialDeductionGameRunner._visible_alive_for_dashboard(state, "Player_1", False) is False


def test_dashboard_shows_public_vote_death_immediately() -> None:
    """公开投票/枪击等死亡不是夜刀剧透，应立即展示出局。"""
    state = State(Vocabulary(frozenset(), frozenset(), frozenset(), frozenset()))
    state.register_entity("GAME", {})
    state.register_entity("Player_1", {})
    writer = StateWriter(state)
    writer.apply(SetAttr("GAME", "round", 1))
    writer.apply(SetAttr("Player_1", "alive", False))
    writer.apply(SetAttr("Player_1", "death_cause", "vote"))
    writer.apply(SetAttr("Player_1", "death_round", 1))

    assert SocialDeductionGameRunner._visible_alive_for_dashboard(state, "Player_1", False) is False
