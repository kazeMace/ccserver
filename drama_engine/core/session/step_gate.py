"""Web-controlled step gate for Drama Engine sessions.

本模块提供 session 级暂停/单步闸门，只依赖 asyncio，不依赖 HTTP 或前端。
Director 在关键推进点调用 ``await gate.wait()``，Web 服务通过本对象控制
继续、暂停和单步放行。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


class WebStepGate:
    """一局游戏的 Web 暂停/单步闸门。"""

    def __init__(self, session_id: str, on_change: Callable[[dict[str, Any]], None] | None = None) -> None:
        """初始化闸门。

        参数：
          session_id — 所属 session ID，用于日志和事件。
          on_change  — 状态变化回调，通常写入 host event store。
        """
        assert session_id, "session_id 不能为空"
        self.session_id = session_id
        self._condition = asyncio.Condition()
        self._step_mode = False
        self._paused = False
        self._permits = 0
        self._closed = False
        self._wait_count = 0
        self._pass_count = 0
        self._waiting_count = 0
        self._on_change = on_change

    @property
    def step_mode(self) -> bool:
        """是否处于单步模式。"""
        return self._step_mode

    @property
    def paused(self) -> bool:
        """是否处于暂停状态。"""
        return self._paused

    async def wait(self) -> None:
        """Director 关键步骤前调用；必要时阻塞直到 Web 放行。"""
        async with self._condition:
            self._wait_count += 1
            if self._can_pass_without_waiting():
                self._pass_count += 1
                return
            self._waiting_count += 1
            self._emit_locked("gate_waiting")
            try:
                while not self._closed:
                    if self._can_consume_step_locked():
                        self._permits -= 1
                        self._pass_count += 1
                        self._emit_locked("gate_step_passed")
                        return
                    if self._can_pass_without_waiting():
                        self._pass_count += 1
                        self._emit_locked("gate_resumed_passed")
                        return
                    await self._condition.wait()
            finally:
                self._waiting_count = max(0, self._waiting_count - 1)
            self._pass_count += 1

    async def set_step_mode(self, enabled: bool) -> dict[str, Any]:
        """开启或关闭单步模式。"""
        async with self._condition:
            self._step_mode = bool(enabled)
            if not self._step_mode:
                self._permits = 0
            self._condition.notify_all()
            event = "step_mode_enabled" if self._step_mode else "step_mode_disabled"
            self._emit_locked(event)
            logger.info("[WebStepGate] %s: session=%s", event, self.session_id)
            return self.status()

    async def step(self, count: int = 1) -> dict[str, Any]:
        """在单步模式下放行 count 个 gate wait 点。"""
        assert count > 0, "count 必须大于 0"
        async with self._condition:
            assert self._step_mode, "只有开启 step mode 后才能 step"
            assert not self._paused, "session 暂停时不能单步；请先 resume"
            self._permits += count
            self._condition.notify_all()
            self._emit_locked("step_permit_added", {"count": count})
            logger.info("[WebStepGate] step: session=%s count=%s permits=%s", self.session_id, count, self._permits)
            return self.status()

    async def pause(self) -> dict[str, Any]:
        """暂停闸门；暂停优先级高于 step permit。"""
        async with self._condition:
            self._paused = True
            self._emit_locked("gate_paused")
            logger.info("[WebStepGate] pause: session=%s", self.session_id)
            return self.status()

    async def resume(self) -> dict[str, Any]:
        """恢复闸门；连续模式会全部放行，单步模式仍需 step permit。"""
        async with self._condition:
            self._paused = False
            self._condition.notify_all()
            self._emit_locked("gate_resumed")
            logger.info("[WebStepGate] resume: session=%s", self.session_id)
            return self.status()

    async def close(self) -> dict[str, Any]:
        """关闭闸门并释放所有等待者。"""
        async with self._condition:
            self._closed = True
            self._condition.notify_all()
            self._emit_locked("gate_closed")
            return self.status()

    async def reset(self) -> dict[str, Any]:
        """恢复到新一局开始前的闸门状态。"""
        async with self._condition:
            self._step_mode = False
            self._paused = False
            self._permits = 0
            self._closed = False
            self._wait_count = 0
            self._pass_count = 0
            self._waiting_count = 0
            self._condition.notify_all()
            self._emit_locked("gate_reset")
            return self.status()

    def status(self) -> dict[str, Any]:
        """返回当前闸门状态。"""
        return {
            "step_mode": self._step_mode,
            "paused": self._paused,
            "permits": self._permits,
            "closed": self._closed,
            "wait_count": self._wait_count,
            "pass_count": self._pass_count,
            "waiting_count": self._waiting_count,
        }

    def _can_pass_without_waiting(self) -> bool:
        """连续模式且未暂停时可直接通过。"""
        return (not self._closed) and (not self._paused) and (not self._step_mode)

    def _can_consume_step_locked(self) -> bool:
        """单步模式下有 permit 且未暂停时可通过。"""
        return (not self._closed) and (not self._paused) and self._step_mode and self._permits > 0

    def _emit_locked(self, kind: str, extra: dict[str, Any] | None = None) -> None:
        """发送 host-only gate 事件。调用方已持有 condition。"""
        if self._on_change is None:
            return
        payload = {
            "kind": kind,
            "gate": self.status(),
        }
        if extra:
            payload.update(extra)
        self._on_change(payload)
