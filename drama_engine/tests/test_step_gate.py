"""Tests for WebStepGate."""

from __future__ import annotations

import asyncio

import pytest

from drama_engine.core.session.step_gate import WebStepGate


@pytest.mark.asyncio
async def test_step_gate_blocks_until_step_permit() -> None:
    """单步模式应阻塞 wait，step 后只放行一个等待点。"""
    events = []
    gate = WebStepGate(session_id="session-step", on_change=events.append)
    await gate.set_step_mode(True)

    task = asyncio.create_task(gate.wait())
    await asyncio.sleep(0.05)
    assert not task.done()
    assert gate.status()["waiting_count"] == 1

    await gate.step()
    await asyncio.wait_for(task, timeout=1)
    assert gate.status()["pass_count"] == 1
    assert any(event["kind"] == "gate_waiting" for event in events)
    assert any(event["kind"] == "gate_step_passed" for event in events)


@pytest.mark.asyncio
async def test_step_gate_pause_resume_in_continuous_mode() -> None:
    """连续模式 pause 后应阻塞，resume 后放行。"""
    gate = WebStepGate(session_id="session-pause")
    await gate.pause()

    task = asyncio.create_task(gate.wait())
    await asyncio.sleep(0.05)
    assert not task.done()

    await gate.resume()
    await asyncio.wait_for(task, timeout=1)
    assert gate.status()["pass_count"] == 1
