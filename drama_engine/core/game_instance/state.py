"""会话状态模型（SessionState）。

本模块只保存「会话过程」状态，不保存「游戏事实」。两者必须分离（见架构文档 §6）：

- SessionState（本文件）：座位、生命周期、当前进度、timeline cursor、checkpoint 列表、
  回滚策略、metadata。它关心「这局会话进行到哪了」。
- GameState（游戏事实）：角色、身份、票数、资产、血量、线索等，由 interactive_session
  runtime 的 `engine.State` 持有。它关心「游戏世界现在是什么样」。

不要把会话进度、事件 cursor、checkpoint 和游戏事实混进同一个对象。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

# 会话生命周期状态 / Session lifecycle statuses
SESSION_LOBBY = "lobby"
SESSION_ASSIGNED = "assigned"
SESSION_RUNNING = "running"
SESSION_PAUSED = "paused"
SESSION_ENDED = "ended"
SESSION_FAILED = "failed"
SESSION_TERMINATED = "terminated"

# 座位控制方式 / Seat controller types
CONTROLLER_AI = "ai"
CONTROLLER_HUMAN = "human"


@dataclass(slots=True)
class SeatState:
    """单局中的一个座位。

    座位是「会话层」概念（谁坐在这个位置、由谁控制），不是「游戏事实」。
    角色/存活等游戏事实的快照字段仅用于视图展示，权威值在 GameState。
    """

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
class ProgressState:
    """会话进度：当前 flow state / scene / round / turn / phase / actor。

    这是「会话进行到哪了」的可序列化快照，供回滚和视图使用；具体玩法语义由
    interactive_session runtime 填充。
    """

    current_state: str | None = None
    current_scene: str | None = None
    round: int = 0
    turn: int = 0
    phase: str | None = None
    actor: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为可序列化字典。"""
        return {
            "current_state": self.current_state,
            "current_scene": self.current_scene,
            "round": self.round,
            "turn": self.turn,
            "phase": self.phase,
            "actor": self.actor,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProgressState":
        """从字典恢复进度状态。"""
        data = dict(data or {})
        return cls(
            current_state=data.get("current_state"),
            current_scene=data.get("current_scene"),
            round=int(data.get("round") or 0),
            turn=int(data.get("turn") or 0),
            phase=data.get("phase"),
            actor=data.get("actor"),
        )


@dataclass(slots=True)
class SessionState:
    """一局游戏的会话过程状态。

    只保存会话过程，不保存游戏事实：座位、生命周期、当前进度、timeline cursor、
    checkpoint 列表、回滚策略、metadata。游戏事实（角色/票数/资产/血量）由
    GameState 持有。
    """

    game_id: str
    script_path: str
    params: dict[str, Any] = field(default_factory=dict)
    seat_ids: list[str] = field(default_factory=list)
    human_seat_ids: set[str] = field(default_factory=set)
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    runtime_type: str = "interactive_session"
    status: str = SESSION_LOBBY
    metadata: dict[str, Any] = field(default_factory=dict)
    seats: dict[str, SeatState] = field(default_factory=dict)
    # 会话进度与回滚相关字段（见架构文档 §6/§7）
    progress: ProgressState = field(default_factory=ProgressState)
    event_cursor: int = 0
    message_cursor: int = 0
    action_cursor: int = 0
    checkpoints: list[str] = field(default_factory=list)
    rollback_policy: str = "branch"

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
        self.progress = ProgressState()
        self.event_cursor = 0
        self.message_cursor = 0
        self.action_cursor = 0
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
            "runtime_type": self.runtime_type,
            "status": self.status,
            "seat_count": len(self.seats),
            "human_seat_count": len([
                seat for seat in self.seats.values()
                if seat.controller_type == CONTROLLER_HUMAN
            ]),
            "progress": self.progress.to_dict(),
            "metadata": dict(self.metadata),
        }

    def to_dict(self) -> dict[str, Any]:
        """转换为持久化字典。"""
        return {
            "session_id": self.session_id,
            "game_id": self.game_id,
            "script_path": self.script_path,
            "runtime_type": self.runtime_type,
            "params": dict(self.params),
            "seat_ids": list(self.seat_ids),
            "human_seat_ids": sorted(self.human_seat_ids),
            "status": self.status,
            "metadata": dict(self.metadata),
            "seats": {seat_id: seat.to_dict() for seat_id, seat in self.seats.items()},
            "progress": self.progress.to_dict(),
            "event_cursor": self.event_cursor,
            "message_cursor": self.message_cursor,
            "action_cursor": self.action_cursor,
            "checkpoints": list(self.checkpoints),
            "rollback_policy": self.rollback_policy,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionState":
        """从持久化字典恢复 SessionState。"""
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
            runtime_type=str(data.get("runtime_type") or "interactive_session"),
            params=dict(data.get("params") or {}),
            seat_ids=seat_ids,
            human_seat_ids=human_seat_ids,
            session_id=str(data.get("session_id") or ""),
            status=str(data.get("status") or SESSION_LOBBY),
            metadata=dict(data.get("metadata") or {}),
            seats=seats,
            progress=ProgressState.from_dict(data.get("progress") or {}),
            event_cursor=int(data.get("event_cursor") or 0),
            message_cursor=int(data.get("message_cursor") or 0),
            action_cursor=int(data.get("action_cursor") or 0),
            checkpoints=list(data.get("checkpoints") or []),
            rollback_policy=str(data.get("rollback_policy") or "branch"),
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
