"""回滚管理（架构文档 §7/§15）。

RollbackManager 根据 checkpoint 恢复会话过程状态、游戏状态、patch journal 与可选记忆，
并在事件流追加 rollback_applied 记录。

回滚策略：
  - soft：标记 checkpoint 之后的记录为 reverted，不物理删除（当前以事件标注实现）。
  - hard：截断 checkpoint 之后的 message/action/event/patch（当前实现按快照整体恢复，
    等价于截断到 checkpoint 时刻）。
  - branch：从 checkpoint 开新分支，保留旧分支用于回放/比较（开发/试玩默认）。

当前实现统一采用「按快照整体恢复」的 hard 语义作为地基，soft/branch 的差异化处理
在需要保留旧分支回放时再扩展；策略值会记录在 rollback_applied 事件中。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.game_instance.snapshots import SessionCheckpoint

logger = logging.getLogger(__name__)


class RollbackManager:
    """按 checkpoint 恢复一局会话到指定时刻。"""

    def __init__(
        self,
        session_control: Any,
        state_provider: Any = None,
        journal_provider: Any = None,
        memory_provider: Any = None,
    ) -> None:
        """绑定恢复目标（与 SnapshotManager 对称）。"""
        assert session_control is not None, "session_control 不能为空"
        self._control = session_control
        self._state_provider = state_provider
        self._journal_provider = journal_provider
        self._memory_provider = memory_provider

    def restore(self, checkpoint: SessionCheckpoint, policy: str = "branch") -> None:
        """把会话恢复到 checkpoint 时刻。

        恢复顺序：SessionState 元数据 → 事件/动作/进度 timeline → GameState →
        PatchJournal → 记忆（可选），最后追加 rollback_applied 事件。
        """
        assert checkpoint is not None, "checkpoint 不能为空"
        assert policy in {"soft", "hard", "branch"}, f"未知 rollback policy: {policy}"
        session = self._control.session_state

        # 1. 恢复会话过程（事件回放/动作 store/进度/cursor）。
        self._control.restore({
            "events": checkpoint.events_snapshot,
            "actions": checkpoint.actions_snapshot,
            "progress": {
                "progress": checkpoint.session_state_snapshot.get("progress", {}),
                "event_cursor": checkpoint.event_cursor,
                "message_cursor": checkpoint.message_cursor,
                "action_cursor": checkpoint.action_cursor,
            },
        })

        # 2. 恢复 SessionState 生命周期/座位快照（进度已由上一步恢复，这里对齐 status）。
        snap = checkpoint.session_state_snapshot
        if snap.get("status"):
            session.set_status(str(snap["status"]))

        # 3. 恢复游戏事实 GameState。
        state = self._state_provider() if self._state_provider is not None else None
        if state is not None and checkpoint.game_state_snapshot:
            state.restore(checkpoint.game_state_snapshot)

        # 4. 恢复 patch journal。
        journal = self._journal_provider() if self._journal_provider is not None else None
        if journal is not None:
            journal.restore(checkpoint.patch_journal_snapshot)

        # 5. 恢复 actor 记忆（可选）。
        memory = self._memory_provider() if self._memory_provider is not None else None
        if memory is not None and checkpoint.actor_memory_snapshot is not None:
            memory.clear()
            memory.load(checkpoint.actor_memory_snapshot)

        # 6. 事件流记录回滚，供 host/回放观察。
        self._control.append_host({
            "kind": "rollback_applied",
            "checkpoint_id": checkpoint.checkpoint_id,
            "reason": checkpoint.reason,
            "policy": policy,
        })
        logger.info(
            "[RollbackManager] 已回滚到 checkpoint=%s policy=%s session=%s",
            checkpoint.checkpoint_id,
            policy,
            session.session_id,
        )


__all__ = ["RollbackManager"]
