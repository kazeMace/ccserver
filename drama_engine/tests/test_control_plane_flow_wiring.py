"""M4：ControlPlane 提案接通执行层的端到端测试。

审查（architecture_review_2026-07.md M4）发现：host/director/writer 提案通过裁定后
写的 patch record type 与 materializer 消费的 "flow_patch" 对不上，提案形同虚设。
本测试验证：控制角色提交的 flow patch 经 runner.apply_flow_patch 真正驱动 flow/state。
"""

from __future__ import annotations

import pytest

from drama_engine.core.game_instance.factory import GameInstanceRegistry

_SCRIPT = "drama_engine/scripts/interactive_session/deduction/werewolf.yaml"


async def _assigned_instance() -> object:
    """创建并 assign 一个声明了 control_plane 的狼人杀实例。"""
    registry = GameInstanceRegistry(store=None, load_existing=False)
    instance = await registry.create_instance(
        game_id="werewolf",
        script_path=_SCRIPT,
        seat_ids=[f"Player_{i}" for i in range(1, 10)],
        params={"dry_run": True, "use_runner": True},
    )
    await instance.assign()
    return instance


@pytest.mark.asyncio
async def test_control_proposal_set_state_drives_game_state() -> None:
    """host 提交 set_state flow patch 提案，通过裁定后真正改到游戏状态（M4 接通）。"""
    instance = await _assigned_instance()
    state = instance._current_game_state()
    assert state is not None

    verdict = instance.submit_control_action(
        role="host",
        payload={
            "kind": "patch",
            "payload": {"type": "set_state", "path": "GAME.host_flag", "value": "raised"},
        },
    )
    assert verdict["approved"] is True, "host 的合法提案应通过裁定"
    # 关键：提案不再形同虚设，而是真正写进了执行层的游戏状态
    assert state.get_attr("GAME", "host_flag") == "raised"


@pytest.mark.asyncio
async def test_control_proposal_add_transition_materializes_into_flow() -> None:
    """host 提交 add_transition 提案后，新 transition 出现在 materialized flow 中。"""
    instance = await _assigned_instance()
    runner = instance.runtime.runner

    # werewolf 是 state_machine，含 night/day 两个 state；加一条 day->night 的显式 transition。
    verdict = instance.submit_control_action(
        role="host",
        payload={
            "kind": "patch",
            "payload": {
                "type": "add_transition",
                "from": "day",
                "to": "night",
                "when": {"left": "GAME.round", "op": "greater_than_equal", "right": 99},
            },
        },
    )
    assert verdict["approved"] is True

    # materialize 后 day state 的 transitions 里应包含新加的 day->night(when round>=99)
    materialized = runner.summary("host")["interactive_session"]["materialized_flow"]
    day_transitions = materialized["flow"]["states"]["day"]["transitions"]
    assert any(
        t.get("to") == "night" and (t.get("when") or {}).get("right") == 99
        for t in day_transitions
    ), "control 提案的 add_transition 应出现在 materialized flow 中"
