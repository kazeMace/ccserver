"""FastAPI app for Drama Engine Web multi-session service."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import asyncio
import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse

from drama_engine.application.catalog import GameCatalog
from drama_engine.core.game_instance.instance import GameInstance
from drama_engine.core.session.persistence import JsonSessionStore
from drama_engine.core.session.registry import SessionRegistry
from drama_engine.service.server.schemas import (
    ClaimSeatRequest,
    CreateSessionRequest,
    PlayerInputRequest,
)

logger = logging.getLogger(__name__)


def create_app(
    registry: SessionRegistry | None = None,
    catalog: GameCatalog | None = None,
) -> FastAPI:
    """创建 Drama Engine FastAPI app。"""
    app = FastAPI(title="Drama Engine", version="0.1.0")
    frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
    if frontend_dir.exists():
        app.mount("/frontend", StaticFiles(directory=str(frontend_dir)), name="drama_frontend")
    app.state.frontend_dir = frontend_dir
    app.state.registry = registry or SessionRegistry(store=JsonSessionStore())
    app.state.catalog = catalog or GameCatalog()

    async def _instance(session_id: str) -> GameInstance:
        """按 session_id 取 GameInstance（service 层唯一入口）。

        GameInstance 缓存在其 runtime 上，保证同一局的 SnapshotManager / checkpoint
        在多次请求间保持一致（否则每次请求新建实例会丢失 checkpoint）。
        """
        try:
            runtime = await app.state.registry.get_session(session_id)
        except AssertionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        instance = getattr(runtime, "_game_instance", None)
        if instance is None:
            instance = GameInstance(runtime)
            setattr(runtime, "_game_instance", instance)
        return instance

    app.state.instance_for = _instance

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """服务健康检查。"""
        return {"status": "ok"}

    @app.get("/")
    async def frontend_index() -> FileResponse:
        """返回创建狼人杀房间页面。"""
        return FileResponse(str(app.state.frontend_dir / "index.html"))

    @app.get("/create")
    async def create_page() -> FileResponse:
        """返回创建狼人杀房间页面。"""
        return FileResponse(str(app.state.frontend_dir / "index.html"))

    @app.get("/host/sessions/{session_id}")
    async def host_page(session_id: str) -> FileResponse:
        """返回 Host 游戏页面。"""
        return FileResponse(str(app.state.frontend_dir / "host.html"))

    @app.get("/player")
    async def player_page() -> FileResponse:
        """返回玩家页面。"""
        return FileResponse(str(app.state.frontend_dir / "player.html"))

    @app.get("/viewer/sessions/{session_id}")
    async def viewer_page(session_id: str) -> FileResponse:
        """返回公开观众页面。"""
        return FileResponse(str(app.state.frontend_dir / "viewer.html"))

    @app.get("/api/frontend/config")
    async def frontend_config() -> dict[str, Any]:
        """返回前端运行配置。"""
        return {
            "title": "Drama Engine 狼人杀",
            "moderatorKey": "__moderator__",
            "roleBadges": {
                "werewolf": "狼人",
                "seer": "预言家",
                "witch": "女巫",
                "hunter": "猎人",
                "guard": "守卫",
                "villager": "村民",
            },
            "scopeStyles": {
                "public": ["#f3f4f6", "#9ca3af", "公开"],
                "town": ["#ecfdf5", "#10b981", "城镇"],
                "wolf-den": ["#fef2f2", "#ef4444", "狼队"],
                "whisper:seer": ["#eef2ff", "#6366f1", "预言家"],
                "whisper:witch": ["#f5f3ff", "#8b5cf6", "女巫"],
                "whisper:guard": ["#eff6ff", "#3b82f6", "守卫"],
            },
        }

    @app.get("/api/games")
    async def list_games() -> list[dict[str, Any]]:
        """列出可创建的游戏。"""
        games = await app.state.catalog.list_games_async()
        return [
            {
                "game_id": game.game_id,
                "script_path": game.script_path,
                "title": game.title,
                "roles": game.roles,
                "recommended_player_role": game.recommended_player_role,
            }
            for game in games
        ]

    @app.post("/api/sessions")
    async def create_session(payload: CreateSessionRequest, http_request: Request) -> dict[str, Any]:
        """创建一局游戏 session。"""
        script_path = payload.script_path
        if script_path is None:
            try:
                game = await app.state.catalog.get_game_async(payload.game_id)
                script_path = game.script_path
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        seat_ids = payload.seat_ids or _default_seat_ids(payload.params)
        runtime = await app.state.registry.create_session(
            game_id=payload.game_id,
            script_path=script_path,
            seat_ids=seat_ids,
            params=payload.params,
            human_seat_ids=set(payload.human_seat_ids),
            metadata=payload.metadata,
        )
        return _runtime_summary_for_request(runtime, http_request)

    @app.get("/api/sessions")
    async def list_sessions() -> list[dict[str, Any]]:
        """列出所有 session。"""
        return await app.state.registry.list_sessions()

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str, http_request: Request) -> dict[str, Any]:
        """获取单个 session 摘要。"""
        try:
            runtime = await app.state.registry.get_session(session_id)
        except AssertionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _runtime_summary_for_request(runtime, http_request)

    @app.post("/api/sessions/{session_id}/assign")
    async def assign_session(session_id: str) -> dict[str, bool]:
        """执行 session 发牌状态流转。"""
        try:
            await app.state.registry.assign_session(session_id)
        except AssertionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/start")
    async def start_session(session_id: str) -> dict[str, bool]:
        """启动 session。"""
        try:
            await app.state.registry.start_session(session_id)
        except AssertionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/restart")
    async def restart_session(session_id: str) -> dict[str, bool]:
        """清局并在同一个 session 中重新发牌。"""
        try:
            await app.state.registry.restart_session(session_id)
        except AssertionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/pause")
    async def pause_session(session_id: str) -> dict[str, bool]:
        """暂停 session。"""
        try:
            await app.state.registry.pause_session(session_id)
        except AssertionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/resume")
    async def resume_session(session_id: str) -> dict[str, bool]:
        """恢复 session。"""
        try:
            await app.state.registry.resume_session(session_id)
        except AssertionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}


    @app.get("/api/sessions/{session_id}/step-gate")
    async def get_step_gate(session_id: str) -> dict[str, Any]:
        """返回 session step gate 状态。"""
        try:
            return await app.state.registry.gate_status(session_id)
        except AssertionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/sessions/{session_id}/step-mode")
    async def set_step_mode(session_id: str, enabled: bool) -> dict[str, Any]:
        """开启或关闭 Web 单步模式。"""
        try:
            gate = await app.state.registry.set_step_mode(session_id, enabled)
        except AssertionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "gate": gate}

    @app.post("/api/sessions/{session_id}/step")
    async def step_session(session_id: str, count: int = 1) -> dict[str, Any]:
        """单步放行 count 个 Director gate wait 点。"""
        try:
            gate = await app.state.registry.step_session(session_id, count=count)
        except AssertionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "gate": gate}

    @app.get("/api/sessions/{session_id}/view/host")
    async def host_view(session_id: str) -> dict[str, Any]:
        """返回主持人视图快照。"""
        instance = await _instance(session_id)
        return instance.host_view()

    @app.get("/api/sessions/{session_id}/view/public")
    async def public_view(session_id: str) -> dict[str, Any]:
        """返回公开观众视图快照。"""
        instance = await _instance(session_id)
        return instance.public_view()

    @app.get("/api/player/view")
    async def player_view(token: str) -> dict[str, Any]:
        """返回玩家视图快照。"""
        claim = app.state.registry.token_service.validate(token)
        if claim is None:
            raise HTTPException(status_code=404, detail="invalid token")
        instance = await _instance(claim.session_id)
        return instance.player_view(claim.seat_id, claim.user_id)

    @app.get("/api/sessions/{session_id}/seats")
    async def get_seats(session_id: str, http_request: Request) -> list[dict[str, Any]]:
        """获取 session seats。"""
        try:
            runtime = await app.state.registry.get_session(session_id)
        except AssertionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _seat_summary_for_request(runtime, http_request)

    @app.get("/api/sessions/{session_id}/pending-actions")
    async def get_pending_actions(session_id: str) -> list[dict[str, Any]]:
        """获取 session pending actions。"""
        instance = await _instance(session_id)
        return instance.pending_actions()

    # —— interaction.v1 归一端点（docs/interaction_protocol_design.md §1）——

    @app.get("/api/sessions/{session_id}/inbox")
    async def get_inbox(session_id: str, seat: str = "public", after: int = 0) -> dict[str, Any]:
        """拉取该 seat 可见的新消息 + 当前待回复（InboxResponse）。

        seat：host | public | audience | player:<id>。after 为上次见过的最大 seq。
        """
        instance = await _instance(session_id)
        return instance.inbox(seat, after=after)

    @app.post("/api/sessions/{session_id}/reply")
    async def post_reply(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """提交 PlayerReply，返回 ReplyAck。body 含 seat_id + request_id + 回复内容。"""
        seat_id = str(body.get("seat_id") or "")
        if not seat_id:
            raise HTTPException(status_code=400, detail="reply 缺少 seat_id")
        instance = await _instance(session_id)
        # seat_id 可能已带 "player:" 前缀（前端传入），避免双重前缀
        seat = seat_id if seat_id.startswith("player:") else f"player:{seat_id}"
        return await instance.reply(seat, body)

    @app.get("/api/sessions/{session_id}/view")
    async def get_state_view(session_id: str, seat: str = "public") -> dict[str, Any]:
        """返回该 seat 的只读状态视图（StateView）。"""
        instance = await _instance(session_id)
        return instance.view(seat)

    @app.post("/api/sessions/{session_id}/moderator/takeover")
    async def moderator_takeover(session_id: str, seat: str) -> dict[str, bool]:
        """主持人将 seat 切换为 AI。"""
        try:
            await app.state.registry.set_seat_controller(session_id, seat, "ai")
        except AssertionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/moderator/set-controller")
    async def moderator_set_controller(session_id: str, seat: str, controller: str, http_request: Request) -> dict[str, Any]:
        """主持人设置 seat 控制方式。"""
        try:
            link = await app.state.registry.set_seat_controller(session_id, seat, controller)
        except AssertionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "join_link": _absolute_url(http_request, link) if link else ""}

    @app.post("/api/sessions/{session_id}/moderator/set-human-count")
    async def moderator_set_human_count(session_id: str, count: int, http_request: Request) -> dict[str, Any]:
        """主持人设置前 N 个 seat 为真人。"""
        try:
            links = await app.state.registry.set_human_count(session_id, count)
        except AssertionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, "player_links": _absolute_links(http_request, links)}

    @app.post("/api/sessions/{session_id}/moderator/reset-link")
    async def moderator_reset_link(session_id: str, seat: str, http_request: Request) -> dict[str, Any]:
        """主持人重置玩家链接。"""
        try:
            link = await app.state.registry.reset_join_link(session_id, seat)
        except AssertionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        absolute = _absolute_url(http_request, link)
        return {"ok": True, "url": absolute, "join_link": absolute}

    @app.post("/api/sessions/{session_id}/moderator/submit")
    async def moderator_submit(session_id: str, seat: str, body: dict[str, Any]) -> dict[str, Any]:
        """主持人代玩家提交当前 pending action。"""
        instance = await _instance(session_id)
        submission = await instance.submit_action(
            seat_id=seat, payload=body.get("data"), source="moderator", text=body.get("text", ""),
        )
        if submission is None:
            raise HTTPException(status_code=409, detail="no pending action")
        return _submission_response(instance.session_id, seat, submission)

    @app.post("/api/sessions/{session_id}/terminate")
    async def terminate_session(session_id: str) -> dict[str, bool]:
        """终止 session。"""
        try:
            await app.state.registry.terminate_session(session_id, reason="api terminate")
        except AssertionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/checkpoint")
    async def create_checkpoint(session_id: str, reason: str = "manual") -> dict[str, Any]:
        """在当前时刻创建一个回滚 checkpoint。"""
        instance = await _instance(session_id)
        return {"ok": True, "checkpoint": instance.checkpoint(reason)}

    @app.get("/api/sessions/{session_id}/rollback-points")
    async def rollback_points(session_id: str) -> list[dict[str, Any]]:
        """返回可回滚的 checkpoint 列表。"""
        instance = await _instance(session_id)
        return instance.rollback_points()

    @app.post("/api/sessions/{session_id}/rollback")
    async def rollback_to(session_id: str, checkpoint_id: str) -> dict[str, bool]:
        """回滚到指定 checkpoint。"""
        instance = await _instance(session_id)
        try:
            await instance.rollback_to(checkpoint_id)
        except AssertionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True}


    @app.get("/api/sessions/{session_id}/events/public")
    async def public_events(session_id: str) -> StreamingResponse:
        """订阅 session public SSE 事件。"""
        instance = await _instance(session_id)
        subscriber = instance.events("public", subscribe=True)
        return StreamingResponse(
            _sse_event_generator(instance.runtime.event_store, subscriber),
            media_type="text/event-stream",
        )

    @app.get("/api/sessions/{session_id}/events/host")
    async def host_events(session_id: str) -> StreamingResponse:
        """订阅 session host SSE 事件。"""
        instance = await _instance(session_id)
        subscriber = instance.events("host", subscribe=True)
        return StreamingResponse(
            _sse_event_generator(instance.runtime.event_store, subscriber),
            media_type="text/event-stream",
        )

    @app.get("/api/player/events")
    async def player_events(token: str) -> StreamingResponse:
        """按玩家 token 订阅 private SSE 事件。"""
        claim = app.state.registry.token_service.validate(token)
        if claim is None:
            raise HTTPException(status_code=404, detail="invalid token")
        instance = await _instance(claim.session_id)
        subscriber = instance.events("private", seat_id=claim.seat_id, subscribe=True)
        return StreamingResponse(
            _sse_event_generator(instance.runtime.event_store, subscriber),
            media_type="text/event-stream",
        )

    @app.get("/api/player/reconnect")
    async def player_reconnect(token: str) -> dict[str, Any]:
        """玩家重连快照：返回该 token 所属 seat 的私密 backlog。"""
        claim = app.state.registry.token_service.validate(token)
        if claim is None:
            raise HTTPException(status_code=404, detail="invalid token")
        instance = await _instance(claim.session_id)
        return {
            "ok": True,
            "session_id": claim.session_id,
            "seat_id": claim.seat_id,
            "backlog": instance.timeline("private", seat_id=claim.seat_id),
            "snapshot": instance.player_view(claim.seat_id, claim.user_id),
        }

    @app.post("/api/player/claim")
    async def claim_player(request: ClaimSeatRequest) -> dict[str, Any]:
        """玩家认领 token。"""
        claim = app.state.registry.token_service.claim(request.token, request.user_id)
        if claim is None:
            raise HTTPException(status_code=404, detail="invalid token")
        instance = await _instance(claim.session_id)
        instance.join_player(claim.seat_id, request.user_id)
        return {
            "ok": True,
            "session_id": claim.session_id,
            "seat_id": claim.seat_id,
            "user_id": request.user_id,
        }

    @app.post("/api/player/input")
    async def submit_player_input(request: PlayerInputRequest) -> dict[str, Any]:
        """玩家提交动作。"""
        claim = app.state.registry.token_service.validate(request.token)
        if claim is None:
            raise HTTPException(status_code=404, detail="invalid token")
        instance = await _instance(claim.session_id)
        submission = await instance.submit_action(
            seat_id=claim.seat_id, payload=request.data, source="human", text=request.text,
        )
        if submission is None:
            raise HTTPException(status_code=409, detail="no pending action")
        return _submission_response(instance.session_id, claim.seat_id, submission)

    return app


def _submission_response(session_id: str, seat_id: str, submission: Any) -> dict[str, Any]:
    """把 ActionSubmission 转成稳定 API 响应。"""
    assert session_id, "session_id 不能为空"
    assert seat_id, "seat_id 不能为空"
    assert submission is not None, "submission 不能为空"
    validated = bool(getattr(submission, "validated", True))
    validation_error = getattr(submission, "validation_error", "")
    return {
        "ok": validated,
        "session_id": session_id,
        "seat_id": getattr(submission, "seat_id", seat_id) or seat_id,
        "request_id": getattr(submission, "request_id", ""),
        "submission_id": getattr(submission, "submission_id", ""),
        "source": getattr(submission, "source", ""),
        "data": getattr(submission, "data", None),
        "text": getattr(submission, "text", ""),
        "validated": validated,
        "validation_error": validation_error,
    }


def _public_base_url(request: Request) -> str:
    """根据请求和代理头推断外部访问 base URL。"""
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")
    proto = (forwarded_proto.split(",")[0].strip() if forwarded_proto else request.url.scheme)
    host = (forwarded_host.split(",")[0].strip() if forwarded_host else request.headers.get("host"))
    assert host, "request host 不能为空"
    return f"{proto}://{host}".rstrip("/")


def _absolute_url(request: Request, path_or_url: str) -> str:
    """把 /player?... 这类相对路径转成绝对 URL。"""
    if not path_or_url:
        return ""
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    if not path_or_url.startswith("/"):
        path_or_url = "/" + path_or_url
    return _public_base_url(request) + path_or_url


def _absolute_links(request: Request, links: dict[str, str]) -> dict[str, str]:
    """批量转换玩家链接为绝对 URL。"""
    return {seat_id: _absolute_url(request, link) for seat_id, link in links.items()}


def _runtime_summary_for_request(runtime: Any, request: Request) -> dict[str, Any]:
    """返回带绝对 URL 的 session summary。"""
    summary = runtime.summary()
    summary["player_links"] = _absolute_links(request, runtime.player_links)
    summary["host_url"] = _absolute_url(request, f"/host/sessions/{runtime.session.session_id}")
    summary["viewer_url"] = _absolute_url(request, f"/viewer/sessions/{runtime.session.session_id}")
    return summary


def _seat_summary_for_request(runtime: Any, request: Request) -> list[dict[str, Any]]:
    """返回带绝对 join_link 的 seats summary。"""
    result = []
    for item in runtime.seat_summary():
        copied = dict(item)
        copied["join_link"] = _absolute_url(request, copied.get("join_link", ""))
        result.append(copied)
    return result


def _default_seat_ids(params: dict[str, Any]) -> list[str]:
    """根据 params 生成默认 seat 列表。"""
    total_players = int(params.get("total_players", 9))
    assert total_players > 0, "total_players 必须大于 0"
    return [f"Player_{index}" for index in range(1, total_players + 1)]

async def _sse_event_generator(event_store: Any, subscriber: Any):
    """把 EventSubscriber queue 转换为 SSE 文本流。"""
    try:
        while True:
            event = await subscriber.queue.get()
            yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            await asyncio.sleep(0)
    finally:
        event_store.unsubscribe(subscriber)
