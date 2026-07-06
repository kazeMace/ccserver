"""GameInstance：一局游戏的应用门面与聚合根（架构文档 §4）。

GameInstance 是 service / API 层唯一应该直接面对的对象。service 层不再直接操作
runtime、runner、event store、action service 或 actor runtime，而是通过 GameInstance
统一进入。

GameInstance 本身不写具体游戏规则；它协调：
  - GameRuntime：底层运行资源与生命周期（assign/start/pause/resume/step/terminate）。
  - SessionControl：会话过程（消息/动作/事件/进度/快照）。
  - ViewProjector：host/player/public 视图（当前复用 view_projection）。
  - SnapshotManager / RollbackManager：回滚（阶段4接入）。
"""

from __future__ import annotations

import logging
from typing import Any

from drama_engine.core.game_instance.session_control import SessionControl
from drama_engine.core.session.view_projection import (
    build_host_snapshot,
    build_player_snapshot,
    build_public_snapshot,
)

logger = logging.getLogger(__name__)


class GameInstance:
    """一局游戏的应用门面。

    通过 GameInstanceFactory 或 registry 创建；持有一个 GameRuntime，并在其之上
    暴露稳定的 service 层接口。
    """

    def __init__(self, runtime: Any) -> None:
        """绑定底层 GameRuntime，并在其会话状态上建立 SessionControl。"""
        assert runtime is not None, "runtime 不能为空"
        assert runtime.service is not None, "runtime.service 不能为空"
        self.runtime = runtime
        self.session_control = SessionControl(
            session_state=runtime.session,
            event_store=runtime.event_store,
            action_service=runtime.action_service,
        )
        logger.info("[GameInstance] 绑定 session=%s", self.session_id)

    # ---- 基本标识 ----

    @property
    def session_id(self) -> str:
        """返回本局 session id。"""
        return self.runtime.session.session_id

    @property
    def status(self) -> str:
        """返回会话生命周期状态。"""
        return self.runtime.session.status

    # ---- 生命周期（委托 GameRuntime）----

    async def assign(self) -> None:
        """执行发牌/初始化。"""
        await self.runtime.assign()

    async def start(self) -> None:
        """启动本局。"""
        await self.runtime.start()

    async def pause(self) -> None:
        """暂停本局。"""
        await self.runtime.pause()

    async def resume(self) -> None:
        """恢复本局。"""
        await self.runtime.resume()

    async def step(self, count: int = 1) -> dict[str, Any]:
        """单步放行 count 个 step gate。"""
        assert count > 0, "count 必须大于 0"
        return await self.runtime.step(count=count)

    async def terminate(self, reason: str = "terminated") -> None:
        """终止本局。"""
        await self.runtime.terminate(reason=reason)

    async def restart(self) -> None:
        """清局并在同一 session 中重新发牌。"""
        await self.runtime.restart()

    # ---- 玩家加入 / 离开 ----

    def join_player(self, seat_id: str, user_id: str) -> None:
        """把 user_id 认领到指定 seat。"""
        assert seat_id, "seat_id 不能为空"
        assert user_id, "user_id 不能为空"
        seats = self.runtime.session.seats
        assert seat_id in seats, f"seat 不存在: {seat_id}"
        seats[seat_id].claimed_by = user_id
        logger.info("[GameInstance] 玩家加入 session=%s seat=%s", self.session_id, seat_id)

    def leave_player(self, seat_id: str, user_id: str) -> None:
        """把 user_id 从指定 seat 释放。"""
        assert seat_id, "seat_id 不能为空"
        seats = self.runtime.session.seats
        assert seat_id in seats, f"seat 不存在: {seat_id}"
        if seats[seat_id].claimed_by == user_id:
            seats[seat_id].claimed_by = None
            logger.info("[GameInstance] 玩家离开 session=%s seat=%s", self.session_id, seat_id)

    # ---- 动作 / 消息 ----

    async def submit_action(
        self,
        seat_id: str,
        payload: dict[str, Any] | None = None,
        source: str = "human",
        text: str = "",
    ) -> Any:
        """提交一个玩家动作到当前 pending request。"""
        return await self.session_control.submit_action(
            seat_id=seat_id,
            source=source,
            data=payload,
            text=text,
        )

    def pending_actions(self) -> list[dict[str, Any]]:
        """返回当前 pending 动作摘要。"""
        return self.session_control.pending_actions()

    def submit_control_action(self, role: str, payload: dict[str, Any]) -> Any:
        """提交控制角色动作（host/director/writer 等）。

        阶段6 ControlPlane 接入后实现；当前显式未支持，避免静默吞掉调用。
        """
        raise NotImplementedError("submit_control_action 将在 ControlPlane 阶段实现")

    def apply_control_proposal(self, proposal: dict[str, Any]) -> Any:
        """应用控制角色 proposal（经 referee/validator 校验后）。

        阶段6 ControlPlane 接入后实现。
        """
        raise NotImplementedError("apply_control_proposal 将在 ControlPlane 阶段实现")

    # ---- 视图（当前复用 view_projection，阶段6 迁移到 ViewProjector）----

    def host_view(self) -> dict[str, Any]:
        """返回主持人视图快照。"""
        return build_host_snapshot(self.runtime).to_dict()

    def public_view(self) -> dict[str, Any]:
        """返回公开观众视图快照。"""
        return build_public_snapshot(self.runtime).to_dict()

    def player_view(self, seat_id: str, user_id: str | None = None) -> dict[str, Any]:
        """返回指定 seat 的玩家视图快照。"""
        assert seat_id, "seat_id 不能为空"
        return build_player_snapshot(self.runtime, seat_id, user_id).to_dict()

    def audience_view(self) -> dict[str, Any]:
        """返回观众视图；当前等同 public_view。"""
        return self.public_view()

    # ---- Timeline / 事件 ----

    def timeline(self, audience: str, seat_id: str | None = None) -> list[dict[str, Any]]:
        """返回指定 audience 的事件 timeline 回放。"""
        assert audience in {"public", "host", "private"}, f"未知 audience: {audience}"
        if audience == "public":
            return self.session_control.public_backlog()
        if audience == "host":
            return self.session_control.host_backlog()
        assert seat_id, "private timeline 必须提供 seat_id"
        return self.session_control.private_backlog(seat_id)

    def events(self, audience: str, seat_id: str | None = None, subscribe: bool = False) -> Any:
        """返回事件回放或订阅对象（供 SSE 使用）。"""
        return self.runtime.events(audience, seat_id=seat_id, subscribe=subscribe)

    # ---- 摘要 ----

    def summary(self) -> dict[str, Any]:
        """返回 session 摘要。"""
        return self.runtime.summary()

    def seat_summary(self) -> list[dict[str, Any]]:
        """返回 seat 摘要。"""
        return self.runtime.seat_summary()

    # ---- 回滚（阶段4接入）----

    def rollback_points(self) -> list[dict[str, Any]]:
        """返回可回滚的 checkpoint 列表。

        阶段4 SnapshotManager/RollbackManager 接入后实现。
        """
        raise NotImplementedError("rollback_points 将在 Checkpoint/Rollback 阶段实现")

    async def rollback_to(self, checkpoint_id: str) -> None:
        """回滚到指定 checkpoint。

        阶段4 接入后实现。
        """
        raise NotImplementedError("rollback_to 将在 Checkpoint/Rollback 阶段实现")


__all__ = ["GameInstance"]
