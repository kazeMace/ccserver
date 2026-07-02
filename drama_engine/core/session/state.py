"""Core data models for Drama Engine Web multi-session runtime."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

SESSION_LOBBY = "lobby"
SESSION_ASSIGNED = "assigned"
SESSION_RUNNING = "running"
SESSION_PAUSED = "paused"
SESSION_ENDED = "ended"
SESSION_FAILED = "failed"
SESSION_TERMINATED = "terminated"

CONTROLLER_AI = "ai"
CONTROLLER_HUMAN = "human"


@dataclass(slots=True)
class SeatState:
    """单局中的一个 seat。"""

    seat_id: str
    controller_type: str = CONTROLLER_AI
    claimed_by: str | None = None
    role_snapshot: str | None = None
    alive_snapshot: bool | None = None

    def __post_init__(self) -> None:
        assert self.seat_id, "seat_id 不能为空"
        assert self.controller_type in {CONTROLLER_AI, CONTROLLER_HUMAN}, (
            f"未知 controller_type: {self.controller_type}"
        )

    def reset_for_new_game(self) -> None:
        """清除本局角色/存活快照，保留控制方式和认领用户。"""
        self.role_snapshot = None
        self.alive_snapshot = None

    def to_dict(self) -> dict[str, Any]:
        """转换为 API 可返回的 dict。"""
        return {
            "seat_id": self.seat_id,
            "controller_type": self.controller_type,
            "claimed_by": self.claimed_by,
            "role_snapshot": self.role_snapshot,
            "alive_snapshot": self.alive_snapshot,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SeatState":
        """从持久化字典恢复 SeatState。"""
        assert isinstance(data, dict), "seat data 必须是 dict"
        return cls(
            seat_id=str(data.get("seat_id") or ""),
            controller_type=str(data.get("controller_type") or CONTROLLER_AI),
            claimed_by=data.get("claimed_by"),
            role_snapshot=data.get("role_snapshot"),
            alive_snapshot=data.get("alive_snapshot"),
        )


@dataclass(slots=True)
class GameSessionState:
    """Web 多会话架构中的一局游戏元状态。"""

    game_id: str
    script_path: str
    params: dict[str, Any] = field(default_factory=dict)
    seat_ids: list[str] = field(default_factory=list)
    human_seat_ids: set[str] = field(default_factory=set)
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = SESSION_LOBBY
    metadata: dict[str, Any] = field(default_factory=dict)
    seats: dict[str, SeatState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        assert self.session_id, "session_id 不能为空"
        assert self.game_id, "game_id 不能为空"
        assert self.script_path, "script_path 不能为空"
        assert self.status in all_session_statuses(), f"未知 session status: {self.status}"
        if not self.seats:
            assert self.seat_ids, "seat_ids 不能为空"
            for seat_id in self.seat_ids:
                controller = CONTROLLER_HUMAN if seat_id in self.human_seat_ids else CONTROLLER_AI
                self.seats[seat_id] = SeatState(seat_id=seat_id, controller_type=controller)

    def set_status(self, status: str) -> None:
        """设置 session 状态。"""
        assert status in all_session_statuses(), f"未知 session status: {status}"
        self.status = status

    def reset_for_new_game(self) -> None:
        """清局重置，保留 seat、控制方式、真人认领与元数据。"""
        for seat in self.seats.values():
            seat.reset_for_new_game()
        self.set_status(SESSION_LOBBY)

    def seat_summary(self) -> list[dict[str, Any]]:
        """返回 seat 摘要。"""
        return [seat.to_dict() for seat in self.seats.values()]

    def to_summary(self) -> dict[str, Any]:
        """返回 session 摘要。"""
        return {
            "session_id": self.session_id,
            "game_id": self.game_id,
            "script_path": self.script_path,
            "status": self.status,
            "seat_count": len(self.seats),
            "human_seat_count": len([
                seat for seat in self.seats.values()
                if seat.controller_type == CONTROLLER_HUMAN
            ]),
            "metadata": dict(self.metadata),
        }

    def to_dict(self) -> dict[str, Any]:
        """转换为持久化字典。"""
        return {
            "session_id": self.session_id,
            "game_id": self.game_id,
            "script_path": self.script_path,
            "params": dict(self.params),
            "seat_ids": list(self.seat_ids),
            "human_seat_ids": sorted(self.human_seat_ids),
            "status": self.status,
            "metadata": dict(self.metadata),
            "seats": {seat_id: seat.to_dict() for seat_id, seat in self.seats.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GameSessionState":
        """从持久化字典恢复 GameSessionState。"""
        assert isinstance(data, dict), "session data 必须是 dict"
        seats_data = data.get("seats") or {}
        seats = {
            str(seat_id): SeatState.from_dict(dict(seat_data))
            for seat_id, seat_data in seats_data.items()
        }
        seat_ids = list(data.get("seat_ids") or seats.keys())
        human_seat_ids = set(data.get("human_seat_ids") or [])
        return cls(
            game_id=str(data.get("game_id") or ""),
            script_path=str(data.get("script_path") or ""),
            params=dict(data.get("params") or {}),
            seat_ids=seat_ids,
            human_seat_ids=human_seat_ids,
            session_id=str(data.get("session_id") or ""),
            status=str(data.get("status") or SESSION_LOBBY),
            metadata=dict(data.get("metadata") or {}),
            seats=seats,
        )


def all_session_statuses() -> set[str]:
    """返回所有合法 session 状态。"""
    return {
        SESSION_LOBBY,
        SESSION_ASSIGNED,
        SESSION_RUNNING,
        SESSION_PAUSED,
        SESSION_ENDED,
        SESSION_FAILED,
        SESSION_TERMINATED,
    }
