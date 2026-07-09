"""Session registry for Drama Engine Web service."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

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
from drama_engine.core.session.persistence import JsonSessionStore
from drama_engine.core.session.controls import ServiceSessionControls
from drama_engine.core.session.factory import _build_action_request_service
from drama_engine.core.session.runtime import GameRuntime
from drama_engine.core.session.step_gate import WebStepGate
from drama_engine.core.session.tokens import PlayerTokenService

logger = logging.getLogger(__name__)

class SessionRegistry:
    """session 注册表。

    默认仍以内存对象服务请求，但可挂载 JsonSessionStore 做持久化恢复。
    """

    def __init__(
        self,
        token_service: PlayerTokenService | None = None,
        store: JsonSessionStore | None = None,
        load_existing: bool = True,
    ) -> None:
        self._sessions: dict[str, GameRuntime] = {}
        self._lock = asyncio.Lock()
        self._token_service = token_service or PlayerTokenService()
        self._store = store
        self._service_controls = ServiceSessionControls()
        if self._store is not None and load_existing:
            self._load_from_store()

    # 终态 session 不恢复也不持久化，防止 registry.json 无限增长
    TERMINAL_STATES = {SESSION_ENDED, SESSION_TERMINATED, SESSION_FAILED}

    def _load_from_store(self) -> None:
        """从持久化 store 恢复 session 和 token（跳过终态 session）。"""
        assert self._store is not None, "store 不能为空"
        snapshot = self._store.load_all()
        if snapshot is None:
            return
        token_data = snapshot.get("tokens")
        if isinstance(token_data, dict):
            self._token_service.load(token_data)
        sessions_data = snapshot.get("sessions") or []
        assert isinstance(sessions_data, list), "sessions 必须是 list"
        skipped = 0
        for item in sessions_data:
            assert isinstance(item, dict), "session snapshot 必须是 dict"
            session_dict = item.get("session") or {}
            if session_dict.get("status") in self.TERMINAL_STATES:
                skipped += 1
                continue
            runtime = self._runtime_from_snapshot(item)
            self._sessions[runtime.session.session_id] = runtime
        logger.info(
            "[SessionRegistry] 从持久化恢复 session 数：%d（跳过终态：%d）",
            len(self._sessions),
            skipped,
        )

    def _save_to_store(self) -> None:
        """保存当前 registry 快照（排除终态 session）。"""
        if self._store is None:
            return
        active_runtimes = [
            runtime for runtime in self._sessions.values()
            if runtime.session.status not in self.TERMINAL_STATES
        ]
        snapshot = {
            "tokens": self._token_service.dump(),
            "sessions": [self._runtime_to_snapshot(runtime) for runtime in active_runtimes],
        }
        self._store.save_all(snapshot)

    def _runtime_to_snapshot(self, runtime: GameRuntime) -> dict[str, Any]:
        """把 runtime 转为可持久化快照。"""
        assert runtime is not None, "runtime 不能为空"
        return {
            "session": runtime.session.to_dict(),
            "player_links": dict(runtime.player_links),
            "events": runtime.event_store.dump(),
            "step_gate": runtime.step_gate.status(),
            "memory": runtime.memory_store.snapshot() if runtime.memory_store is not None else {},
            "actions": runtime.action_service.service_action.dump()
            if hasattr(runtime.action_service, "service_action")
            else {},
        }

    def _runtime_from_snapshot(self, snapshot: dict[str, Any]) -> GameRuntime:
        """从持久化快照恢复 runtime。"""
        assert isinstance(snapshot, dict), "runtime snapshot 必须是 dict"
        session = SessionState.from_dict(dict(snapshot.get("session") or {}))
        if session.status in {SESSION_RUNNING, SESSION_PAUSED}:
            old_status = session.status
            session.set_status(SESSION_ASSIGNED)
            session.metadata["restored_from_status"] = old_status
            session.metadata["restore_note"] = "service restarted; running task was not persisted"
        event_store = SessionEventStore(session.session_id)
        event_data = snapshot.get("events")
        if isinstance(event_data, dict):
            event_store.load(event_data)
        step_gate = WebStepGate(session_id=session.session_id, on_change=event_store.append_host)
        runtime = GameRuntime(
            session=session,
            event_store=event_store,
            action_service=_build_action_request_service(session),
            player_links=dict(snapshot.get("player_links") or {}),
            step_gate=step_gate,
        )
        self._attach_service_ports(runtime)
        memory_data = snapshot.get("memory")
        if isinstance(memory_data, dict) and runtime.memory_store is not None:
            runtime.memory_store.load(memory_data)
        action_data = snapshot.get("actions")
        if isinstance(action_data, dict) and hasattr(runtime.action_service, "service_action"):
            runtime.action_service.service_action.load(action_data)
        if bool(session.params.get("use_runner", True)):
            from drama_engine.core.runner import build_runner_for_session

            runtime.register_runner(
                build_runner_for_session(
                    runtime=runtime,
                    dry_run=bool(session.params.get("dry_run", True)),
                )
            )
        return runtime

    @property
    def token_service(self) -> PlayerTokenService:
        """返回玩家 token 服务。"""
        return self._token_service

    async def create_session(
        self,
        game_id: str,
        script_path: str,
        seat_ids: list[str],
        params: dict[str, Any] | None = None,
        human_seat_ids: set[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GameRuntime:
        """创建一局游戏。"""
        assert game_id, "game_id 不能为空"
        assert script_path, "script_path 不能为空"
        assert seat_ids, "seat_ids 不能为空"
        session = SessionState(
            game_id=game_id,
            script_path=script_path,
            params=dict(params or {}),
            seat_ids=list(seat_ids),
            human_seat_ids=set(human_seat_ids or set()),
            status=SESSION_LOBBY,
            metadata=dict(metadata or {}),
        )
        player_links = {}
        for seat_id in sorted(session.human_seat_ids):
            token = self._token_service.create_token(session.session_id, seat_id)
            player_links[seat_id] = f"/player?token={token}"
        event_store = SessionEventStore(session.session_id)
        step_gate = WebStepGate(
            session_id=session.session_id,
            on_change=event_store.append_host,
        )
        runtime = GameRuntime(
            session=session,
            event_store=event_store,
            action_service=_build_action_request_service(session),
            player_links=player_links,
            step_gate=step_gate,
        )
        self._attach_service_ports(runtime)
        if bool((params or {}).get("use_runner", True)):
            from drama_engine.core.runner import build_runner_for_session

            runtime.register_runner(
                build_runner_for_session(
                    runtime=runtime,
                    dry_run=bool((params or {}).get("dry_run", True)),
                )
            )
        async with self._lock:
            self._sessions[session.session_id] = runtime
            self._save_to_store()
        logger.info("[SessionRegistry] 创建 session：%s", session.session_id)
        return runtime

    def _attach_service_ports(self, runtime: GameRuntime) -> None:
        """Bind registry-owned service resources to runtime service ports."""
        assert runtime is not None, "runtime 不能为空"
        assert runtime.service is not None, "runtime.service 不能为空"
        runtime.service.token_service = self._token_service
        runtime.service.persistence = self._store

    async def get_session(self, session_id: str) -> GameRuntime:
        """获取指定 session。"""
        assert session_id, "session_id 不能为空"
        async with self._lock:
            runtime = self._sessions.get(session_id)
        assert runtime is not None, f"session 不存在: {session_id}"
        return runtime

    async def list_sessions(self) -> list[dict[str, Any]]:
        """列出所有 session 摘要。"""
        async with self._lock:
            runtimes = list(self._sessions.values())
        return [runtime.summary() for runtime in runtimes]


    def _instance_for(self, runtime: GameRuntime) -> Any:
        """取得（或惰性创建并缓存）该 runtime 的 GameInstance 门面。

        生命周期必须经门面而非直连 runtime：GameInstance.assign/restart 会按脚本
        重建 KnowledgeFirewall 与 ControlPlane（H1 缺口1 的修复点）。缓存挂在 runtime
        上（与 service/server/app.py 同款约定），保证 checkpoint / firewall 跨请求一致。

        延迟 import 避免 registry ← factory ← instance ← registry 的循环导入。
        """
        assert runtime is not None, "runtime 不能为空"
        instance = getattr(runtime, "_game_instance", None)
        if instance is None:
            from drama_engine.core.game_instance.factory import GameInstanceFactory

            instance = GameInstanceFactory.wrap(runtime)
            setattr(runtime, "_game_instance", instance)
        return instance

    async def assign_session(self, session_id: str) -> None:
        """对指定 session 执行发牌状态流转（经 GameInstance，触发 firewall 重建）。"""
        runtime = await self.get_session(session_id)
        # 从 metadata 读取角色分配信息
        role_assignments = runtime.session.metadata.get("role_assignments")
        if role_assignments and isinstance(role_assignments, dict):
            await self._instance_for(runtime).assign(role_assignments=role_assignments)
        else:
            await self._instance_for(runtime).assign()
        self._save_to_store()

    async def start_session(self, session_id: str) -> None:
        """启动指定 session（经 GameInstance）。"""
        runtime = await self.get_session(session_id)
        await self._instance_for(runtime).start()
        self._save_to_store()

    async def restart_session(self, session_id: str) -> None:
        """在同一个 session 中清局并重新发牌（经 GameInstance，触发 firewall 重建）。"""
        runtime = await self.get_session(session_id)
        await self._instance_for(runtime).restart()
        self._save_to_store()

    async def pause_session(self, session_id: str) -> None:
        """暂停指定 session（经 GameInstance）。"""
        runtime = await self.get_session(session_id)
        await self._instance_for(runtime).pause()
        self._save_to_store()

    async def resume_session(self, session_id: str) -> None:
        """恢复指定 session（经 GameInstance）。"""
        runtime = await self.get_session(session_id)
        await self._instance_for(runtime).resume()
        self._save_to_store()

    async def set_step_mode(self, session_id: str, enabled: bool) -> dict[str, Any]:
        """开启或关闭指定 session 的单步模式。"""
        runtime = await self.get_session(session_id)
        result = await runtime.step_gate.set_step_mode(enabled)
        self._save_to_store()
        return result

    async def step_session(self, session_id: str, count: int = 1) -> dict[str, Any]:
        """对指定 session 放行 count 个 step gate（经 GameInstance）。"""
        runtime = await self.get_session(session_id)
        result = await self._instance_for(runtime).step(count=count)
        self._save_to_store()
        return result

    async def gate_status(self, session_id: str) -> dict[str, Any]:
        """返回指定 session 的 step gate 状态。"""
        runtime = await self.get_session(session_id)
        return runtime.step_gate.status()

    async def set_seat_controller(self, session_id: str, seat_id: str, controller_type: str) -> str:
        """设置 seat 控制方式。"""
        runtime = await self.get_session(session_id)
        link = self._service_controls.set_controller(runtime, seat_id, controller_type, self._token_service)
        self._save_to_store()
        return link

    async def set_human_count(self, session_id: str, count: int) -> dict[str, str]:
        """设置本局前 N 个 seat 为真人。"""
        runtime = await self.get_session(session_id)
        links = self._service_controls.set_human_count(runtime, count, self._token_service)
        self._save_to_store()
        return links

    async def reset_join_link(self, session_id: str, seat_id: str) -> str:
        """重置 seat 加入链接。"""
        runtime = await self.get_session(session_id)
        link = self._service_controls.reset_join_link(runtime, seat_id, self._token_service)
        self._save_to_store()
        return link

    async def terminate_session(self, session_id: str, reason: str = "terminated") -> None:
        """终止指定 session。"""
        runtime = await self.get_session(session_id)
        await runtime.terminate(reason=reason)
        self._save_to_store()

__all__ = ["SessionRegistry"]
