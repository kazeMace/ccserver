"""Tests for shared action timeout policy components."""

from __future__ import annotations

import pytest

from drama_engine.core.ports.timeout import ACTION_KIND_VOTE, TIMEOUT_ABSTAIN, TimeoutPolicy
from drama_engine.core.session.actions import ActionRequestService as ServiceActionRequestService
from drama_engine.core.ports.actions import ServiceActionPort


def test_timeout_policy_uses_configured_vote_policy() -> None:
    """TimeoutPolicy 应按配置返回 vote 超时策略。"""
    policy = TimeoutPolicy.from_dict({"vote": TIMEOUT_ABSTAIN})
    assert policy.policy_for_kind(ACTION_KIND_VOTE) == TIMEOUT_ABSTAIN


@pytest.mark.asyncio
async def test_service_action_service_uses_shared_timeout_resolver() -> None:
    """service action 过期动作应使用共享 timeout resolver 的默认提交。"""
    service = ServiceActionRequestService(
        session_id="timeout-session",
        timeout_policy=TimeoutPolicy(vote=TIMEOUT_ABSTAIN),
    )
    request = service.create_request(
        seat_id="Player_1",
        cue="请选择投票目标",
        kind=ACTION_KIND_VOTE,
        candidates=["Player_2"],
    )

    submission = await service.expire_request(request.request_id)

    assert submission.source == "timeout_default"
    assert submission.data == {"action": False, "target": None}
    assert submission.text == "弃权（超时）"
    assert submission.validated is True


@pytest.mark.asyncio
async def test_service_action_port_exposes_deadline_and_metadata() -> None:
    """Runtime action view should expose deadline/metadata for player UX."""
    service = ServiceActionRequestService("session-action-meta")
    request = service.create_request(
        seat_id="Player_1",
        cue="请选择",
        kind="generic",
        schema={"type": "object", "properties": {"choice": {"enum": ["A", "B"]}}},
        metadata={"scene_name": "choose", "scene_display_name": "选择", "timeout_seconds": 30},
    )
    action = ServiceActionPort(service).current_action("Player_1")

    assert action["request_id"] == request.request_id
    assert action["metadata"]["scene_display_name"] == "选择"
    assert action["scene_name"] == "choose"
    assert action["timeout_seconds"] == 30
    assert action["deadline_at"] is not None
