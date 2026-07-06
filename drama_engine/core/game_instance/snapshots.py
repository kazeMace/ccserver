"""会话检查点与快照管理（架构文档 §7）。

回滚模型采用 checkpoint + append-only timeline：所有消息/动作/事件/patch 都 append-only，
在关键节点创建 checkpoint。回滚时恢复 checkpoint，再按策略处理其后的 timeline。

SessionCheckpoint 至少包含：会话状态、游戏状态、runtime 状态、patch journal、
event/message/action cursor 与可选 actor 记忆快照。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SessionCheckpoint:
    """一个会话检查点。

    字段对齐架构文档 §7：
      - session_state_snapshot：会话过程状态（座位/生命周期/进度）。
      - game_state_snapshot：游戏事实（engine.State.full_snapshot）。
      - runtime_state_snapshot：runtime 轻量状态（phase 等）。
      - patch_journal_snapshot：patch journal 快照。
      - event/message/action_cursor：timeline 截断位置。
      - actor_memory_snapshot：可选的 actor 记忆快照。
    """

    checkpoint_id: str
    reason: str
    created_at: str
    session_state_snapshot: dict[str, Any]
    game_state_snapshot: dict[str, Any]
    runtime_state_snapshot: dict[str, Any]
    patch_journal_snapshot: list[dict[str, Any]]
    event_cursor: int
    message_cursor: int
    action_cursor: int
    events_snapshot: dict[str, Any] = field(default_factory=dict)
    actions_snapshot: dict[str, Any] = field(default_factory=dict)
    actor_memory_snapshot: dict[str, Any] | None = None
    # 披露账本快照：回滚时一并恢复（截断语义，与 patch journal 对称）。
    disclosure_ledger_snapshot: list[dict[str, Any]] = field(default_factory=list)

    def to_summary(self) -> dict[str, Any]:
        """返回面向 API 的轻量摘要（不含完整状态体）。"""
        return {
            "checkpoint_id": self.checkpoint_id,
            "reason": self.reason,
            "created_at": self.created_at,
            "event_cursor": self.event_cursor,
            "message_cursor": self.message_cursor,
            "action_cursor": self.action_cursor,
        }


class SnapshotManager:
    """在关键节点创建 SessionCheckpoint，并保存 checkpoint 列表。

    快照来源：
      - SessionControl.snapshot()：会话过程（session/events/actions/progress）。
      - GameState（engine.State.full_snapshot）：游戏事实，由调用方传入 state provider。
      - PatchJournal.snapshot()：patch journal，由调用方传入 journal provider。
      - memory store（可选）。
    """

    def __init__(
        self,
        session_control: Any,
        state_provider: Any = None,
        journal_provider: Any = None,
        memory_provider: Any = None,
        disclosure_provider: Any = None,
        clock: Any = None,
    ) -> None:
        """绑定快照来源。

        参数：
          session_control     — SessionControl 实例。
          state_provider      — 无参可调用，返回当前 engine.State（可为 None，例如 assign 前）。
          journal_provider    — 无参可调用，返回当前 PatchJournal（可为 None）。
          memory_provider     — 无参可调用，返回 RuntimeMemoryStore（可为 None）。
          disclosure_provider — 无参可调用，返回当前 DisclosureLedger（可为 None）。
          clock               — 无参可调用，返回 ISO 时间字符串；便于测试注入确定性时间。
        """
        assert session_control is not None, "session_control 不能为空"
        self._control = session_control
        self._state_provider = state_provider
        self._journal_provider = journal_provider
        self._memory_provider = memory_provider
        self._disclosure_provider = disclosure_provider
        self._clock = clock or _default_clock
        self._checkpoints: dict[str, SessionCheckpoint] = {}
        self._order: list[str] = []
        self._counter = 0

    def create_checkpoint(self, reason: str) -> SessionCheckpoint:
        """在当前时刻创建一个 checkpoint。"""
        assert reason, "reason 不能为空"
        session = self._control.session_state
        control_snapshot = self._control.snapshot()

        state = self._state_provider() if self._state_provider is not None else None
        game_state_snapshot = state.full_snapshot() if state is not None else {}

        journal = self._journal_provider() if self._journal_provider is not None else None
        patch_snapshot = journal.snapshot() if journal is not None else []

        memory = self._memory_provider() if self._memory_provider is not None else None
        memory_snapshot = memory.snapshot() if memory is not None else None

        ledger = self._disclosure_provider() if self._disclosure_provider is not None else None
        disclosure_snapshot = ledger.snapshot() if ledger is not None else []

        self._counter += 1
        checkpoint_id = f"ckpt-{self._counter}"
        checkpoint = SessionCheckpoint(
            checkpoint_id=checkpoint_id,
            reason=reason,
            created_at=self._clock(),
            session_state_snapshot=control_snapshot.get("session", session.to_dict()),
            game_state_snapshot=game_state_snapshot,
            runtime_state_snapshot=self._runtime_state_snapshot(),
            patch_journal_snapshot=patch_snapshot,
            event_cursor=session.event_cursor,
            message_cursor=session.message_cursor,
            action_cursor=session.action_cursor,
            events_snapshot=control_snapshot.get("events", {}),
            actions_snapshot=control_snapshot.get("actions", {}),
            actor_memory_snapshot=memory_snapshot,
            disclosure_ledger_snapshot=disclosure_snapshot,
        )
        self._checkpoints[checkpoint_id] = checkpoint
        self._order.append(checkpoint_id)
        session.checkpoints = list(self._order)
        logger.info(
            "[SnapshotManager] 创建 checkpoint=%s reason=%s session=%s",
            checkpoint_id,
            reason,
            session.session_id,
        )
        return checkpoint

    def _runtime_state_snapshot(self) -> dict[str, Any]:
        """采集 runtime 轻量状态（phase 等），无 runtime 时返回空。"""
        runtime = getattr(self._control, "runtime", None)
        runtime_state = getattr(runtime, "runtime_state", None) if runtime is not None else None
        if runtime_state is None:
            return {}
        return {"phase": getattr(runtime_state, "phase", None)}

    def get(self, checkpoint_id: str) -> SessionCheckpoint:
        """按 id 获取 checkpoint。"""
        assert checkpoint_id in self._checkpoints, f"checkpoint 不存在: {checkpoint_id}"
        return self._checkpoints[checkpoint_id]

    def list_points(self) -> list[dict[str, Any]]:
        """返回 checkpoint 摘要列表（按创建顺序）。"""
        return [self._checkpoints[cid].to_summary() for cid in self._order]

    def has(self, checkpoint_id: str) -> bool:
        """判断 checkpoint 是否存在。"""
        return checkpoint_id in self._checkpoints


def _default_clock() -> str:
    """返回当前 ISO 时间字符串。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


__all__ = ["SessionCheckpoint", "SnapshotManager"]
