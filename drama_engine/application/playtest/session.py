"""Admin playtest session manager.

试玩测试属于管理开发端功能，默认 dry_run/step_mode。第一版已经接入真实
SessionRegistry：创建 playtest 时会创建一局 use_runner=True、dry_run=True 的
Drama Engine runtime session。这样管理端试玩不是空壳，同时仍与普通玩家入口隔离。
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from drama_engine.core.session.registry import SessionRegistry

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PlaytestSession:
    """A lightweight admin playtest session mapped to a real runtime session."""

    playtest_id: str
    script_id: str
    script_path: str
    runtime_session_id: str
    mode: str = "dry_run"
    human_player_count: int = 0
    step_mode: bool = True
    status: str = "created"
    current_step: int = 0
    created_at: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PlaytestManager:
    """In-memory playtest manager for admin service.

    参数 / Args:
        registry: SessionRegistry used to create real dry-run runtime sessions.
    """

    def __init__(self, registry: SessionRegistry | None = None) -> None:
        self._sessions: dict[str, PlaytestSession] = {}
        self._registry = registry or SessionRegistry(store=None, load_existing=False)

    async def create(
        self,
        script_id: str,
        script_path: str,
        mode: str = "dry_run",
        human_player_count: int = 0,
        step_mode: bool = True,
    ) -> PlaytestSession:
        """Create one playtest session and its real runtime session."""
        assert script_id, "script_id 不能为空"
        assert script_path, "script_path 不能为空"
        assert human_player_count >= 0, "human_player_count 不能为负数"
        seat_ids = [f"Player_{index}" for index in range(1, 13)]
        human_seat_ids = set(seat_ids[:human_player_count])
        runtime = await self._registry.create_session(
            game_id=f"playtest:{script_id}",
            script_path=script_path,
            seat_ids=seat_ids,
            human_seat_ids=human_seat_ids,
            params={
                "total_players": 12,
                "werewolf_count": 4,
                "dry_run": True,
                "use_runner": True,
            },
            metadata={"admin_playtest": True, "script_id": script_id},
        )
        if step_mode:
            await self._registry.set_step_mode(runtime.session.session_id, True)
        session = PlaytestSession(
            playtest_id=f"pt_{uuid4().hex[:12]}",
            script_id=script_id,
            script_path=script_path,
            runtime_session_id=runtime.session.session_id,
            mode=mode or "dry_run",
            human_player_count=human_player_count,
            step_mode=step_mode,
            created_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        )
        session.events.append({"kind": "playtest_created", "message": "试玩测试已创建，并已绑定真实试运行会话。", "step": 0})
        session.events.append({"kind": "runtime_session", "message": runtime.session.session_id, "step": 0})
        self._sessions[session.playtest_id] = session
        logger.info("[PlaytestManager] created playtest=%s runtime=%s", session.playtest_id, runtime.session.session_id)
        return session

    def get(self, playtest_id: str) -> PlaytestSession:
        """Return one playtest session."""
        assert playtest_id, "playtest_id 不能为空"
        if playtest_id not in self._sessions:
            raise KeyError(f"试玩不存在: {playtest_id}")
        return self._sessions[playtest_id]

    def list(self) -> list[PlaytestSession]:
        """List playtest sessions."""
        return list(self._sessions.values())

    async def assign(self, playtest_id: str) -> PlaytestSession:
        """Assign roles in the runtime session."""
        session = self.get(playtest_id)
        await self._registry.assign_session(session.runtime_session_id)
        session.status = "assigned"
        session.events.append({"kind": "runtime_assigned", "step": session.current_step, "message": "真实试运行会话已完成发牌。"})
        return session

    async def start(self, playtest_id: str) -> PlaytestSession:
        """Start the runtime session."""
        session = self.get(playtest_id)
        await self._registry.start_session(session.runtime_session_id)
        session.status = "running"
        session.events.append({"kind": "runtime_started", "step": session.current_step, "message": "真实试运行会话已启动。"})
        return session

    async def step(self, playtest_id: str, count: int = 1) -> PlaytestSession:
        """Release step gate tokens and record playtest progress."""
        assert count > 0, "count 必须大于 0"
        session = self.get(playtest_id)
        await self._registry.step_session(session.runtime_session_id, count)
        session.status = "running"
        session.current_step += count
        session.events.append({
            "kind": "runtime_step_released",
            "step": session.current_step,
            "message": f"已向真实单步闸门放行 {count} 步。",
        })
        return session

    async def reset(self, playtest_id: str) -> PlaytestSession:
        """Reset bookkeeping; runtime session remains available for inspection."""
        session = self.get(playtest_id)
        session.current_step = 0
        session.status = "created"
        session.events.append({"kind": "playtest_reset", "step": 0, "message": "试玩测试计数已重置；如需全新运行会话，请重新创建试玩。"})
        return session

    async def runtime_summary(self, playtest_id: str) -> dict[str, Any]:
        """Return mapped runtime session summary."""
        session = self.get(playtest_id)
        runtime = await self._registry.get_session(session.runtime_session_id)
        return runtime.summary()
