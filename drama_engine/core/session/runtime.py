"""GameRuntime：一局游戏的底层运行资源容器。

它管理 task、event publisher、action router、memory store、step gate、actor runtime、
runtime state 和 lifecycle hooks；不负责创建游戏 API、加入玩家、HTML 视图、消息回滚或
具体游戏规则（那些属于后续的 GameInstance / SessionControl / GameRunner）。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from drama_engine.core.session.actions import ActionRequestService
from drama_engine.core.session.events import SessionEventStore
from drama_engine.core.game_instance.state import (
    SESSION_ASSIGNED,
    SESSION_ENDED,
    SESSION_FAILED,
    SESSION_LOBBY,
    SESSION_PAUSED,
    SESSION_RUNNING,
    SESSION_TERMINATED,
    SessionState,
)
from drama_engine.core.ports.actions import RuntimeActionServiceRouter, RuntimeActionView
from drama_engine.core.ports.events import EventPublisher
from drama_engine.core.ports.input import InputBridge
from drama_engine.core.ports.memory import RuntimeMemoryStore
from drama_engine.core.runner.base import BasicGameRunner
from drama_engine.core.runner.config import RuntimeConfigParser
from drama_engine.core.actors import ActorRuntime
from drama_engine.core.session.lifecycle import RuntimeLifecycleHooks, RuntimeState
from drama_engine.core.session.ports import ServicePorts
from drama_engine.core.session.summary import SummaryProvider
from drama_engine.core.session.step_gate import WebStepGate

logger = logging.getLogger(__name__)

@dataclass(slots=True)
class GameRuntime:
    """一局派对游戏的运行时控制容器。"""

    session: SessionState
    event_store: SessionEventStore
    action_service: ActionRequestService | RuntimeActionServiceRouter
    player_links: dict[str, str]
    step_gate: WebStepGate
    runner: BasicGameRunner | None = None
    actor_runtime: ActorRuntime | None = field(default=None)
    input_bridge: InputBridge | None = None
    event_publisher: EventPublisher | None = None
    action_view: RuntimeActionView | None = None
    runtime_config_parser: RuntimeConfigParser | None = None
    lifecycle_hooks: RuntimeLifecycleHooks | None = None
    memory_store: RuntimeMemoryStore | None = None
    summary_provider: SummaryProvider | None = None
    service: ServicePorts | None = None
    runtime_state: RuntimeState | None = None
    # service 层缓存的 GameInstance（懒创建），保证同一局 checkpoint 跨请求一致。
    _game_instance: Any = field(default=None, repr=False)
    _director_task: asyncio.Task[Any] | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        assert self.session is not None, "session 不能为空"
        assert self.event_store is not None, "event_store 不能为空"
        assert self.action_service is not None, "action_service 不能为空"
        assert self.step_gate is not None, "step_gate 不能为空"
        assert self.session.session_id == self.event_store.session_id, "event_store session_id 不一致"
        assert self.session.session_id == self.action_service.session_id, "action_service session_id 不一致"
        if not isinstance(self.action_service, RuntimeActionServiceRouter):
            self.action_service = RuntimeActionServiceRouter(
                runtime=self,
                service_action=self.action_service,
            )
        if self.service is None:
            self.service = ServicePorts(
                session_state=self.session,
                event_sink=self.event_store,
                action_view=self.action_service,
            )
        if self.actor_runtime is None:
            self.actor_runtime = ActorRuntime(runtime=self)
        if self.input_bridge is None:
            self.input_bridge = InputBridge()
        if self.event_publisher is None:
            self.event_publisher = EventPublisher(self.service.event_sink)
        if self.action_view is None:
            self.action_view = RuntimeActionView(self.action_service)
        if self.runtime_config_parser is None:
            self.runtime_config_parser = RuntimeConfigParser()
        if self.lifecycle_hooks is None:
            self.lifecycle_hooks = RuntimeLifecycleHooks()
        if self.memory_store is None:
            self.memory_store = RuntimeMemoryStore()
        if self.summary_provider is None:
            self.summary_provider = SummaryProvider()
        if self.runtime_state is None:
            self.runtime_state = RuntimeState()
        if self._director_task is not None:
            self.runtime_state.task = self._director_task

    @property
    def director_task(self) -> asyncio.Task[Any] | None:
        """Compatibility task alias for older service/tests."""
        if self.runtime_state is None:
            return self._director_task
        return self.runtime_state.task

    @director_task.setter
    def director_task(self, task: asyncio.Task[Any] | None) -> None:
        """Compatibility task alias for older service/tests."""
        if self.runtime_state is None:
            self.runtime_state = RuntimeState()
        self.runtime_state.task = task
        self._director_task = task

    def register_runner(self, runner: BasicGameRunner) -> BasicGameRunner:
        """挂载或替换本局的游戏 runner。"""
        assert isinstance(runner, BasicGameRunner), "runner 必须是 BasicGameRunner"
        assert runner.runtime is self, "runner.runtime 必须指向当前 runtime"
        self.runner = runner
        if self.runtime_state is None:
            self.runtime_state = RuntimeState()
        self.runtime_state.metadata = dict(self.runtime_state.metadata or {})
        self.runtime_state.metadata["runner"] = runner.__class__.__name__
        logger.info(
            "[GameRuntime] 已注册 runner：session=%s runner=%s",
            self.session.session_id,
            runner.__class__.__name__,
        )
        return runner

    def events(
        self,
        audience: str,
        seat_id: str | None = None,
        subscribe: bool = False,
    ) -> Any:
        """返回事件回放或订阅对象。

        参数：
          audience  — public / host / private
          seat_id   — private audience 对应的 seat
          subscribe — True 时返回 EventSubscriber，False 时返回 backlog list
        """
        assert audience in {"public", "host", "private"}, f"未知 audience: {audience}"
        if audience == "public":
            return self.event_store.subscribe_public() if subscribe else self.event_store.public_backlog()
        if audience == "host":
            return self.event_store.subscribe_host() if subscribe else self.event_store.host_backlog()
        assert seat_id, "private events 必须提供 seat_id"
        return (
            self.event_store.subscribe_private(seat_id)
            if subscribe
            else self.event_store.private_backlog(seat_id)
        )

    def summary(self) -> dict[str, Any]:
        """返回 session 摘要。"""
        assert self.summary_provider is not None, "summary_provider 不能为空"
        return self.summary_provider.session_summary(self)

    def seat_summary(self) -> list[dict[str, Any]]:
        """返回带加入链接的 seat 摘要。"""
        assert self.summary_provider is not None, "summary_provider 不能为空"
        return self.summary_provider.seat_summary(self)

    def host_summary(self) -> dict[str, Any]:
        """返回主持人视角摘要。"""
        assert self.summary_provider is not None, "summary_provider 不能为空"
        return self.summary_provider.audience_summary(self, audience="host")

    def public_summary(self) -> dict[str, Any]:
        """返回公开视角摘要。"""
        assert self.summary_provider is not None, "summary_provider 不能为空"
        return self.summary_provider.audience_summary(self, audience="public")

    def player_summary(self, seat_id: str) -> dict[str, Any]:
        """返回玩家视角摘要。"""
        assert seat_id, "seat_id 不能为空"
        assert self.summary_provider is not None, "summary_provider 不能为空"
        return self.summary_provider.audience_summary(self, audience="player", seat_id=seat_id)

    async def assign(self) -> None:
        """执行本局发牌。"""
        self._set_runtime_phase("assigning")
        await self._emit_lifecycle("before", "assign")
        if self.runner is not None:
            await self.runner.assign()
            self._set_runtime_phase("assigned")
            await self._emit_lifecycle("after", "assign")
            return
        assert self.session.status == SESSION_LOBBY, (
            f"只有 lobby 状态可以 assign，当前: {self.session.status}"
        )
        self.session.set_status(SESSION_ASSIGNED)
        self.event_store.append_host({"kind": "session_assigned"})
        self.event_store.append_public({"kind": "session_assigned"})
        self._set_runtime_phase("assigned")
        await self._emit_lifecycle("after", "assign")
        logger.info("[GameRuntime] 已发牌：session=%s", self.session.session_id)

    async def start(self) -> None:
        """启动本局。"""
        self._set_runtime_phase("starting")
        await self._emit_lifecycle("before", "start")
        if self.runner is not None:
            await self.runner.start()
            self._set_runtime_phase("running")
            await self._emit_lifecycle("after", "start")
            return
        assert self.session.status == SESSION_ASSIGNED, (
            f"只有 assigned 状态可以 start，当前: {self.session.status}"
        )
        self.session.set_status(SESSION_RUNNING)
        self.event_store.append_host({"kind": "session_started"})
        self.event_store.append_public({"kind": "session_started"})
        self._set_runtime_phase("running")
        await self._emit_lifecycle("after", "start")
        logger.info("[GameRuntime] 已开始：session=%s", self.session.session_id)

    async def pause(self) -> None:
        """暂停本局。"""
        self._set_runtime_phase("pausing")
        await self._emit_lifecycle("before", "pause")
        assert self.session.status == SESSION_RUNNING, (
            f"只有 running 状态可以 pause，当前: {self.session.status}"
        )
        if self.runner is not None:
            await self.runner.pause()
        await self.step_gate.pause()
        self.session.set_status(SESSION_PAUSED)
        assert self.event_publisher is not None, "event_publisher 不能为空"
        self.event_publisher.host({"kind": "session_paused"})
        self.event_publisher.public({"kind": "session_paused"})
        self._set_runtime_phase("paused")
        await self._emit_lifecycle("after", "pause")
        logger.info("[GameRuntime] 已暂停：session=%s", self.session.session_id)

    async def resume(self) -> None:
        """恢复本局。"""
        self._set_runtime_phase("resuming")
        await self._emit_lifecycle("before", "resume")
        assert self.session.status == SESSION_PAUSED, (
            f"只有 paused 状态可以 resume，当前: {self.session.status}"
        )
        if self.runner is not None:
            await self.runner.resume()
        await self.step_gate.resume()
        self.session.set_status(SESSION_RUNNING)
        assert self.event_publisher is not None, "event_publisher 不能为空"
        self.event_publisher.host({"kind": "session_resumed"})
        self.event_publisher.public({"kind": "session_resumed"})
        self._set_runtime_phase("running")
        await self._emit_lifecycle("after", "resume")
        logger.info("[GameRuntime] 已恢复：session=%s", self.session.session_id)

    async def step(self, count: int = 1) -> dict[str, Any]:
        """放行 runner 的统一单步入口。"""
        previous_phase = self.runtime_state.phase if self.runtime_state is not None else "idle"
        self._set_runtime_phase("stepping")
        try:
            await self._emit_lifecycle("before", "step", {"count": count})
            assert count > 0, "count 必须大于 0"
            if self.runner is not None:
                await self.runner.step(count=count)
            else:
                await self.step_gate.step(count=count)
        finally:
            self._set_runtime_phase(previous_phase)
        await self._emit_lifecycle("after", "step", {"count": count})
        return self.step_gate.status()

    def mark_ended(self) -> None:
        """标记本局正常结束。"""
        self.action_service.cancel_all()
        self.session.set_status(SESSION_ENDED)
        self.event_store.append_host({"kind": "session_ended"})
        self.event_store.append_public({"kind": "session_ended"})
        self._set_runtime_phase("ended")
        logger.info("[GameRuntime] 已结束：session=%s", self.session.session_id)

    def mark_failed(self, reason: str) -> None:
        """标记本局异常失败。"""
        assert reason, "reason 不能为空"
        self.action_service.cancel_all()
        self.session.set_status(SESSION_FAILED)
        self.event_store.append_host({"kind": "session_failed", "reason": reason})
        self._set_runtime_phase("failed")
        logger.info("[GameRuntime] 已失败：session=%s reason=%s", self.session.session_id, reason)

    async def terminate(self, reason: str = "terminated by host") -> None:
        """终止本局，并取消后台任务和 pending action。"""
        self._set_runtime_phase("terminating")
        await self._emit_lifecycle("before", "terminate", {"reason": reason})
        if self.runner is not None:
            await self.runner.terminate(reason=reason)
        elif self.director_task is not None and not self.director_task.done():
            self.director_task.cancel()
        self.action_service.cancel_all()
        self.session.set_status(SESSION_TERMINATED)
        self.event_store.append_host({"kind": "session_terminated", "reason": reason})
        self._set_runtime_phase("terminated")
        await self._emit_lifecycle("after", "terminate", {"reason": reason})
        logger.info("[GameRuntime] 已终止：session=%s", self.session.session_id)

    async def restart(self) -> None:
        """清空当前局并在同一个 session 中重新发牌。

        保留内容：session_id、seat、真人/AI 控制方式、玩家链接和认领用户。
        清空内容：Director task、pending action、角色/存活快照、事件回放、step gate。
        """
        self._set_runtime_phase("restarting")
        await self._emit_lifecycle("before", "restart")
        if self.runner is not None and hasattr(self.runner, "reset_runtime_state"):
            await self.runner.reset_runtime_state()
        elif self.director_task is not None and not self.director_task.done():
            self.director_task.cancel()
            try:
                await self.director_task
            except asyncio.CancelledError:
                pass
            self.director_task = None
        self.action_service.cancel_all()
        if self.actor_runtime is not None:
            self.actor_runtime.reset()
        if self.memory_store is not None:
            self.memory_store.clear()
        self.session.reset_for_new_game()
        self.event_store.clear_backlog()
        await self.step_gate.reset()
        self.event_store.append_host({"kind": "session_restarted"})
        self.event_store.append_public({"kind": "session_restarted"})
        await self.assign()
        await self._emit_lifecycle("after", "restart")
        logger.info("[GameRuntime] 已重新开始：session=%s", self.session.session_id)

    def _set_runtime_phase(self, phase: str) -> None:
        """更新 runtime 自身生命周期 phase。"""
        assert phase, "phase 不能为空"
        if self.runtime_state is None:
            self.runtime_state = RuntimeState()
        self.runtime_state.phase = phase

    async def _emit_lifecycle(
        self,
        phase: str,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Emit a runtime lifecycle hook if hooks are configured."""
        assert phase in {"before", "after"}, f"未知 lifecycle phase: {phase}"
        assert action, "action 不能为空"
        if self.lifecycle_hooks is None:
            return
        await self.lifecycle_hooks.emit(
            event_name=f"{phase}_{action}",
            runtime=self,
            action=action,
            payload=payload,
        )

__all__ = ["GameRuntime"]
