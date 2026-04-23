#!/usr/bin/env python3
"""
CCServer — FastAPI with SSE, WebSocket, and plain HTTP endpoints.
"""

import os
import sys
from dotenv import load_dotenv
load_dotenv()

# ── 启动时校验 PROJECT_DIR（必须在业务导入之前）────────────────────────────────
if not os.getenv("CCSERVER_PROJECT_DIR"):
    print(
        "[ERROR] CCSERVER_PROJECT_DIR is not set.\n"
        "Set it to the project workspace directory before starting the API server.\n"
        "Example: CCSERVER_PROJECT_DIR=/path/to/project python server.py",
        file=sys.stderr,
    )
    sys.exit(1)

from pathlib import Path
_project_dir = Path(os.environ["CCSERVER_PROJECT_DIR"]).resolve()
if not _project_dir.exists():
    print(
        f"[ERROR] CCSERVER_PROJECT_DIR='{_project_dir}' does not exist.",
        file=sys.stderr,
    )
    sys.exit(1)

# ── 业务导入 ────────────────────────────────────────────────────────────────────
import asyncio
import json
import uuid
from typing import AsyncIterator, Optional

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ccserver import (
    AgentRunner,
    Session,
    SessionManager,
    SESSIONS_BASE,
    Pipeline,
)
from ccserver.config import (
    DB_PATH, SYSTEM_FILE, APPEND_SYSTEM, STORAGE_BACKEND,
    MONGO_URI, MONGO_DB, REDIS_URL, REDIS_CACHE_SIZE, REDIS_TTL,
)
from ccserver.storage import build_storage
from ccserver.emitters.sse import SSEEmitter
from ccserver.emitters.ws import WSEmitter
from ccserver.emitters.collect import CollectEmitter
from ccserver.emitters.filter import FilterEmitter, VALID_VERBOSITY
from ccserver.emitters.tui import gradient_text
from ccserver.team import TeamHealthMonitor
from ccserver.team.mailbox import TeamMailbox
from ccserver.team.models import TeamMemberRole
from loguru import logger
from ccserver.log import setup_logging
from contextlib import asynccontextmanager
import time as _time

# Logo 最先输出（print 不经过 logging，不受 sink 影响）
# 仅在直接运行时打印，uvicorn worker reload 时 __name__ != "__main__"，不会重复
if __name__ == "__main__":
    from ccserver.emitters.tui import gradient_text as _gt
    _LOGO_START = (255, 111, 0)
    _LOGO_END   = (0,   119, 182)
    for _line in [
        "  ██████╗ ██████╗███████╗███████╗██████╗ ██╗   ██╗███████╗██████╗ ",
        "  ██╔════╝██╔════╝██╔════╝██╔════╝██╔══██╗██║   ██║██╔════╝██╔══██╗",
        "  ██║     ██║     ███████╗█████╗  ██████╔╝██║   ██║█████╗  ██████╔╝",
        "  ██║     ██║     ╚════██║██╔══╝  ██╔══██╗╚██╗ ██╔╝██╔══╝  ██╔══██╗",
        "  ╚██████╗╚██████╗███████║███████╗██║  ██║ ╚████╔╝ ███████╗██║  ██║",
        "   ╚═════╝ ╚═════╝╚══════╝╚══════╝╚═╝  ╚═╝  ╚═══╝  ╚══════╝╚═╝  ╚═╝",
    ]:
        print(_gt(_line, _LOGO_START, _LOGO_END))
    print(f"\n\033[2m  API Server — http://0.0.0.0:8000  |  project: {_project_dir}\033[0m\n")

setup_logging(stderr=True)

# ─── App setup ────────────────────────────────────────────────────────────────

_storage = build_storage(
    STORAGE_BACKEND, SESSIONS_BASE, DB_PATH,
    mongo_uri=MONGO_URI, mongo_db=MONGO_DB,
    redis_url=REDIS_URL, redis_cache_size=REDIS_CACHE_SIZE, redis_ttl=REDIS_TTL,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("server startup | CONVERSATION_ID={} PROJECT_DIR={}",
                os.environ.get("CONVERSATION_ID", "<unset>"),
                os.environ.get("CCSERVER_PROJECT_DIR", "<unset>"))
    # startup：MongoDB 连通性检查（仅 mongo backend）
    if hasattr(_storage, "ping"):
        await _storage.ping()
    # 启动 Team 健康监控（自愈）
    _team_monitor.start()
    yield
    # shutdown：Team 监控停止，然后关闭连接
    _team_monitor.stop()
    if hasattr(_storage, "close"):
        await _storage.close()


app = FastAPI(title="CCServer", version="1.0.0", lifespan=lifespan)

# ─── HTTP 访问日志 middleware ──────────────────────────────────────────────────
# 环境变量 CCSERVER_ACCESS_LOG=0 可关闭（默认开启）
_ACCESS_LOG_ENABLED = os.environ.get("CCSERVER_ACCESS_LOG", "1") != "0"

if _ACCESS_LOG_ENABLED:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as _Request

    class _AccessLogMiddleware(BaseHTTPMiddleware):
        """
        记录每条 HTTP 请求的方法、路径、状态码和耗时。
        颜色分层：2xx=绿, 3xx=蓝, 4xx=黄, 5xx=红。
        设置环境变量 CCSERVER_ACCESS_LOG=0 可完全关闭。
        """

        # ANSI 颜色代码（不依赖 loguru 标签，直接嵌入消息字符串）
        _GREEN   = "\033[32m"
        _BLUE    = "\033[34m"
        _YELLOW  = "\033[33m"
        _RED     = "\033[31m"
        _RESET   = "\033[0m"
        _DIM     = "\033[2m"

        def _status_color(self, status: int) -> str:
            """根据 HTTP 状态码返回对应 ANSI 颜色前缀。"""
            if status < 300:
                return self._GREEN
            if status < 400:
                return self._BLUE
            if status < 500:
                return self._YELLOW
            return self._RED

        async def dispatch(self, request: _Request, call_next):
            t0 = _time.perf_counter()
            response = await call_next(request)
            elapsed_ms = (_time.perf_counter() - t0) * 1000

            status = response.status_code
            color  = self._status_color(status)
            dim    = self._DIM
            reset  = self._RESET

            # 格式：METHOD  /path  → 200  12.3ms
            # 颜色直接写入消息，loguru 原样透传到终端
            logger.info(
                "{dim}{method:<6}{reset}  {path}  →  {color}{status}{reset}  {dim}{ms:.1f}ms{reset}",
                dim=dim, reset=reset,
                method=request.method,
                path=request.url.path,
                color=color,
                status=status,
                ms=elapsed_ms,
            )
            return response

    app.add_middleware(_AccessLogMiddleware)

session_manager = SessionManager(SESSIONS_BASE, storage=_storage)
_team_monitor = TeamHealthMonitor(session_manager)

# SSE 会话的 emitter 注册表：session_id → SSEEmitter
# 用于 AskUserQuestion：客户端通过 POST /chat/stream/answer 将答案注入正在等待的 emitter。
_sse_emitters: dict[str, "SSEEmitter"] = {}


def _read_system_file(path: str | None) -> str | None:
    """启动时读取 system prompt 文件，返回文本内容。"""
    if not path:
        return None
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"CCSERVER_SYSTEM_FILE 不存在: {path}")
    return p.read_text(encoding="utf-8")


runner = AgentRunner(system=_read_system_file(SYSTEM_FILE), append_system=APPEND_SYSTEM)


def _wrap_emitter(
    inner,
    verbosity: str = "verbose",
    stream: bool = True,
    interactive: bool = True,
) -> FilterEmitter:
    """用 FilterEmitter 包装内部 emitter，统一应用三个输出控制参数。"""
    return FilterEmitter(inner, verbosity=verbosity, stream=stream, interactive=interactive)


# ─── Request / Response schemas ───────────────────────────────────────────────


class CreateSessionRequest(BaseModel):
    session_id: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    dag: bool = False                   # True = Pipeline 模式，False = 单 Agent 模式（默认）
    pipeline_class: str | None = None   # dag=True 时指定 pipeline 类名（预留，暂未使用）
    verbosity: str = "verbose"          # 展示详细程度："verbose"（全过程）或 "final_only"（只看结果）
    stream: bool = True                 # 是否推送 token 流；False 时只推送工具调用等结构化事件
    interactive: bool = True            # 是否等待用户交互；False 时 permission 自动拒绝、ask_user 直接跳过
                                        # verbosity="final_only" 时强制为 False


class CreateTeamRequest(BaseModel):
    name: str
    lead_name: str | None = None


class AddMemberRequest(BaseModel):
    name: str
    role: str = "teammate"


class SendTeamMessageRequest(BaseModel):
    to: str
    text: str
    summary: str | None = None


class MarkReadRequest(BaseModel):
    msg_ids: list[str]


# ─── Session routes ───────────────────────────────────────────────────────────


@app.post("/sessions", summary="Create a new session with an isolated workdir")
def create_session(req: CreateSessionRequest):
    session = session_manager.create(req.session_id)
    return session.to_meta()


@app.get("/sessions", summary="List all sessions")
def list_sessions():
    return session_manager.list_all()


@app.get("/sessions/{session_id}", summary="Get session metadata")
def get_session(session_id: str):
    session = _get_or_404(session_id)
    return {**session.to_meta(), "message_count": len(session.messages)}


# ─── Shell 后台任务查询 ─────────────────────────────────────────────────────────


@app.get(
    "/sessions/{session_id}/tasks",
    summary="List all shell background tasks for a session",
)
def list_shell_tasks(session_id: str):
    """
    返回当前 Session 中所有后台 shell 任务的状态。

    返回结构：
        tasks: list[ShellTaskState.to_dict()]  — 按注册顺序排列
        summary: ShellTaskRegistry.summary()     — 各状态计数

    示例：
        GET /sessions/abc123/tasks
        → {
            "tasks": [
                {"id": "b3f2a1c0", "type": "local_bash", "status": "running",
                 "command": "npm run build", "pid": 12345, ...},
                {"id": "b1a2b3c4", "type": "local_bash", "status": "completed",
                 "command": "echo hi", "exit_code": 0, ...}
            ],
            "summary": {"total": 2, "running": 1, "completed": 1, ...}
        }
    """
    session = _get_or_404(session_id)
    tasks = session.shell_tasks.list_all()
    return {
        "tasks": [t.to_dict() for t in tasks],
        "summary": session.shell_tasks.summary(),
    }


@app.post(
    "/sessions/{session_id}/tasks/{task_id}/kill",
    summary="Kill a running shell background task",
)
def kill_shell_task(session_id: str, task_id: str):
    """
    主动终止一个运行中的后台 shell 任务。

    相当于向该进程发送 SIGKILL，进程立即终止。
    任务状态变为 "killed"。

    返回：
        200: {"status": "ok", "message": "Task killed."}
        404: 任务不存在
        409: 任务不在 running 状态（已完成或已终止）

    示例：
        POST /sessions/abc123/tasks/b3f2a1c0/kill
        → {"status": "ok", "message": "Task killed."}
    """
    session = _get_or_404(session_id)
    task = session.shell_tasks.get(task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"Task '{task_id}' not found in session '{session_id}'.",
        )
    if not task.is_running:
        raise HTTPException(
            status_code=409,
            detail=f"Task '{task_id}' is '{task.status}', not running. Cannot kill.",
        )
    ok = session.shell_tasks.kill(task_id, reason="http kill request")
    if not ok:
        raise HTTPException(
            status_code=500,
            detail=f"Kill failed unexpectedly for task '{task_id}'.",
        )
    return {"status": "ok", "message": "Task killed."}


@app.get(
    "/sessions/{session_id}/tasks/{task_id}",
    summary="Get a single shell background task by ID",
)
def get_shell_task(session_id: str, task_id: str):
    """
    根据 task_id 查询单个任务的详细状态。

    路径参数：
        session_id: Session 唯一标识
        task_id:    任务 ID（格式 "b" + uuid 前 8 位）

    返回 404 如果任务不存在。

    示例：
        GET /sessions/abc123/tasks/b3f2a1c0
        → {"id": "b3f2a1c0", "type": "local_bash", "status": "running",
           "command": "npm run build", "pid": 12345, "output": "Compiling...",
           "start_time": "2026-04-12T...", "exit_code": null, ...}
    """
    session = _get_or_404(session_id)
    task = session.shell_tasks.get(task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"Task '{task_id}' not found in session '{session_id}'.",
        )
    return task.to_dict()


# ─── Agent 后台任务查询 ────────────────────────────────────────────────────────


@app.get(
    "/sessions/{session_id}/agent-tasks",
    summary="List all agent background tasks for a session",
)
def list_agent_tasks(session_id: str):
    """
    返回当前 Session 中所有后台 Agent 任务的状态。

    返回结构：
        tasks: list[AgentTaskState.to_dict()]  — 按注册顺序排列
        summary: AgentTaskRegistry.summary()       — 各状态计数

    示例：
        GET /sessions/abc123/agent-tasks
        → {
            "tasks": [
                {"id": "a3f2a1c0", "type": "local_agent", "status": "running",
                 "agent_id": "...", "agent_name": "coder", ...},
            ],
            "summary": {"total": 1, "running": 1, "completed": 0, ...}
        }
    """
    session = _get_or_404(session_id)
    tasks = session.agent_tasks.list_all()
    return {
        "tasks": [t.to_dict() for t in tasks],
        "summary": session.agent_tasks.summary(),
    }


@app.get(
    "/sessions/{session_id}/agent-tasks/{task_id}",
    summary="Get a single agent background task by ID",
)
def get_agent_task(session_id: str, task_id: str):
    """
    根据 task_id 查询单个 Agent 任务的详细状态。

    路径参数：
        session_id: Session 唯一标识
        task_id:    Agent 任务 ID（格式 "a" + uuid 前 8 位）

    返回 404 如果任务不存在。

    示例：
        GET /sessions/abc123/agent-tasks/a3f2a1c0
        → {"id": "a3f2a1c0", "type": "local_agent", "status": "completed",
           "agent_id": "...", "agent_name": "coder", "result": "...",
           "start_time": "2026-04-12T...", "end_time": "2026-04-12T...", ...}
    """
    session = _get_or_404(session_id)
    task = session.agent_tasks.get(task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"Agent task '{task_id}' not found in session '{session_id}'.",
        )
    return task.to_dict()


@app.post(
    "/sessions/{session_id}/agent-tasks/{task_id}/cancel",
    summary="Cancel a running agent background task",
)
async def cancel_agent_task(session_id: str, task_id: str):
    """
    主动取消一个运行中的后台 Agent 任务。

    向 asyncio.Task 发送 CancelledError，Agent 协程退出。
    任务状态变为 "cancelled"。

    注意：此接口只负责取消 Agent 协程，不管理 Shell 任务。
          若 Agent 内部启动了 shell 后台任务，需通过
          POST /sessions/{session_id}/tasks/{task_id}/kill 单独终止。

    返回：
        200: {"status": "ok", "message": "Agent task cancelled."}
        404: 任务不存在
        409: 任务不在 running 状态（已完成或已取消）

    示例：
        POST /sessions/abc123/agent-tasks/a3f2a1c0/cancel
        → {"status": "ok", "message": "Agent task cancelled."}
    """
    session = _get_or_404(session_id)
    task = session.agent_tasks.get(task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"Agent task '{task_id}' not found in session '{session_id}'.",
        )
    if not task.is_running:
        raise HTTPException(
            status_code=409,
            detail=f"Agent task '{task_id}' is '{task.status}', not running. Cannot cancel.",
        )
    # Agent 任务通过 BackgroundAgentHandle 取消
    from ccserver.agent_registry import get_handle
    handle = get_handle(task.agent_id)
    if handle is not None:
        asyncio.create_task(handle.cancel())
    else:
        # fallback：直接标记为 cancelled
        task.mark_cancelled()
    return {"status": "ok", "message": "Agent task cancelled."}


# ─── Team routes ──────────────────────────────────────────────────────────────


def _get_team_or_404(session, team_name: str):
    """辅助函数：获取 team，若不存在返回 404。"""
    registry = session.team_registry
    if registry is None:
        raise HTTPException(
            status_code=404,
            detail="Team feature is not enabled for this session.",
        )
    team = registry.get_team(team_name)
    if team is None:
        raise HTTPException(
            status_code=404,
            detail=f"Team '{team_name}' not found.",
        )
    return team


def _role_from_str(role: str) -> TeamMemberRole:
    """将字符串角色映射到 TeamMemberRole 枚举。"""
    mapping = {
        "lead": TeamMemberRole.LEAD,
        "teammate": TeamMemberRole.TEAMMATE,
    }
    r = mapping.get(role.lower())
    if r is None:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role '{role}'. Must be 'lead' or 'teammate'.",
        )
    return r


@app.get(
    "/sessions/{session_id}/teams",
    summary="List all teams in a session",
)
def list_teams(session_id: str):
    """返回当前 Session 中的所有团队列表。"""
    session = _get_or_404(session_id)
    registry = session.team_registry
    if registry is None:
        return {"teams": []}
    return {
        "teams": [
            {
                "name": t.name,
                "lead_id": t.lead_id,
                "member_count": len(t.members),
                "allowed_paths": t.allowed_paths,
            }
            for t in registry.list_teams()
        ],
    }


@app.post(
    "/sessions/{session_id}/teams",
    summary="Create a new team",
)
def create_team(session_id: str, req: CreateTeamRequest):
    """在指定 Session 中创建一个新团队。"""
    session = _get_or_404(session_id)
    registry = session.team_registry
    if registry is None:
        raise HTTPException(
            status_code=404,
            detail="Team feature is not enabled for this session.",
        )
    team = registry.create_team(req.name.strip(), lead_name=req.lead_name)
    return {
        "name": team.name,
        "lead_id": team.lead_id,
        "members": {k: v.to_dict() for k, v in team.members.items()},
    }


@app.get(
    "/sessions/{session_id}/teams/{team_name}",
    summary="Get team details",
)
def get_team(session_id: str, team_name: str):
    """获取指定团队的详细信息，包括所有成员。"""
    session = _get_or_404(session_id)
    team = _get_team_or_404(session, team_name)
    return {
        "name": team.name,
        "lead_id": team.lead_id,
        "allowed_paths": team.allowed_paths,
        "members": {k: v.to_dict() for k, v in team.members.items()},
    }


@app.delete(
    "/sessions/{session_id}/teams/{team_name}",
    summary="Delete a team",
)
def delete_team(session_id: str, team_name: str):
    """删除指定团队（仅移除注册表与持久化数据，不影响正在运行的 Agent）。"""
    session = _get_or_404(session_id)
    registry = session.team_registry
    if registry is None:
        raise HTTPException(
            status_code=404,
            detail="Team feature is not enabled for this session.",
        )
    registry.delete_team(team_name)
    return {"status": "ok", "message": f"Team '{team_name}' deleted."}


@app.get(
    "/sessions/{session_id}/teams/{team_name}/members",
    summary="List team members",
)
def list_members(session_id: str, team_name: str):
    """返回团队成员列表。"""
    session = _get_or_404(session_id)
    team = _get_team_or_404(session, team_name)
    return {
        "members": [m.to_dict() for m in team.members.values()],
    }


@app.post(
    "/sessions/{session_id}/teams/{team_name}/members",
    summary="Add a member to a team",
)
def add_member(session_id: str, team_name: str, req: AddMemberRequest):
    """向指定团队添加一名成员。"""
    session = _get_or_404(session_id)
    registry = session.team_registry
    if registry is None:
        raise HTTPException(
            status_code=404,
            detail="Team feature is not enabled for this session.",
        )
    role = _role_from_str(req.role)
    member = registry.add_member(team_name, req.name.strip(), role=role)
    return {"member": member.to_dict()}


@app.delete(
    "/sessions/{session_id}/teams/{team_name}/members/{agent_id}",
    summary="Remove a member from a team",
)
def remove_member(session_id: str, team_name: str, agent_id: str):
    """从团队中移除指定成员。"""
    session = _get_or_404(session_id)
    registry = session.team_registry
    if registry is None:
        raise HTTPException(
            status_code=404,
            detail="Team feature is not enabled for this session.",
        )
    registry.remove_member(team_name, agent_id)
    return {"status": "ok", "message": f"Member '{agent_id}' removed."}


@app.get(
    "/sessions/{session_id}/teams/{team_name}/health",
    summary="Team health check",
)
def team_health(session_id: str, team_name: str):
    """
    返回团队后台组件的健康状态：
      - dispatcher_alive: TeamTaskDispatcher 是否在运行
      - relay_alive: TeamPermissionRelay 是否在运行
      - pollers_alive: 各 MailboxPoller 是否在运行
    """
    session = _get_or_404(session_id)
    team = _get_team_or_404(session, team_name)

    dispatcher = getattr(team, "_dispatcher", None)
    relay = getattr(team, "_relay", None)

    pollers: dict[str, bool] = {}
    from ccserver import agent_registry
    for handle in agent_registry.list_handles():
        poller = getattr(handle, "_team_poller", None)
        if poller is None:
            continue
        # 只统计属于当前团队的 poller
        if handle.agent_id.endswith(f"@{team_name}"):
            pollers[handle.agent_id] = poller.is_alive

    return {
        "team_name": team_name,
        "dispatcher_alive": dispatcher.is_alive if dispatcher else False,
        "relay_alive": relay.is_alive if relay else False,
        "pollers_alive": pollers,
    }


# ─── Mailbox routes ───────────────────────────────────────────────────────────


@app.post(
    "/sessions/{session_id}/teams/{team_name}/mailbox/send",
    summary="Send a mailbox message to a teammate",
)
def send_mailbox_message(
    session_id: str,
    team_name: str,
    req: SendTeamMessageRequest,
):
    """
    向 Team 中某个成员的 Mailbox 发送消息。
    to="*" 表示广播（排除发送者自己时由业务层处理）。
    """
    session = _get_or_404(session_id)
    team = _get_team_or_404(session, team_name)
    mailbox = TeamMailbox(team_name, session.storage)

    from ccserver.team.protocol import ChatMessage

    if req.to == "*":
        # 广播给所有成员
        recipients = list(team.members.keys())
        chat_msg = ChatMessage(
            from_agent="http_client",
            to_agent="*",
            text=req.text,
            summary=req.summary,
        )
        mailbox.broadcast(chat_msg, recipients=recipients)
        return {"status": "ok", "broadcast_to": len(recipients)}
    else:
        to_agent = f"{req.to}@{team_name}"
        if to_agent not in team.members:
            raise HTTPException(
                status_code=404,
                detail=f"Teammate '{req.to}' not found in team '{team_name}'.",
            )
        chat_msg = ChatMessage(
            from_agent="http_client",
            to_agent=to_agent,
            text=req.text,
            summary=req.summary,
        )
        mailbox.send(chat_msg)
        return {"status": "ok", "to": to_agent}


@app.get(
    "/sessions/{session_id}/teams/{team_name}/mailbox/{agent_id}",
    summary="Fetch mailbox messages for a teammate",
)
def fetch_mailbox_messages(
    session_id: str,
    team_name: str,
    agent_id: str,
    unread_only: bool = True,
    limit: int = 100,
):
    """获取指定成员的 Mailbox 消息列表。"""
    session = _get_or_404(session_id)
    _get_team_or_404(session, team_name)
    mailbox = TeamMailbox(team_name, session.storage)
    msgs = mailbox.fetch_messages(agent_id, unread_only=unread_only, limit=limit)
    return {
        "messages": [m.to_dict() for m in msgs],
        "count": len(msgs),
    }


@app.post(
    "/sessions/{session_id}/teams/{team_name}/mailbox/{agent_id}/read",
    summary="Mark mailbox messages as read",
)
def mark_mailbox_read(
    session_id: str,
    team_name: str,
    agent_id: str,
    req: MarkReadRequest,
):
    """将指定消息标记为已读。"""
    session = _get_or_404(session_id)
    _get_team_or_404(session, team_name)
    mailbox = TeamMailbox(team_name, session.storage)
    mailbox.mark_read(agent_id, req.msg_ids)
    return {"status": "ok", "marked_count": len(req.msg_ids)}


# ─── AskUserQuestion: 注入答案（SSE 模式专用）────────────────────────────────────


class AnswerRequest(BaseModel):
    answer: str


@app.post(
    "/chat/stream/answer",
    summary="Inject user answer for a pending AskUserQuestion (SSE mode)",
)
async def inject_answer(req: AnswerRequest, x_session_id: str = Header(..., alias="X-Session-Id")):
    """
    当 SSE 流中出现 `{"type": "ask_user", "questions": [...]}` 事件时，
    说明 agent 正在等待用户回答。客户端应：
      1. 展示问题给用户
      2. 收集用户选择
      3. 调用此接口将答案注入，agent 循环将继续执行

    请求体：{ "answer": "用户的回答内容" }
    Header：X-Session-Id 必须与正在等待的 SSE 会话一致
    """
    emitter = _sse_emitters.get(x_session_id)
    if emitter is None:
        raise HTTPException(
            status_code=404,
            detail=f"No active SSE session for '{x_session_id}', or agent is not waiting for input.",
        )
    emitter.inject_answer(req.answer)
    return {"status": "ok"}


class PermissionRequest(BaseModel):
    granted: bool   # True = 批准工具执行，False = 拒绝


@app.post(
    "/chat/stream/permission",
    summary="Respond to a pending tool permission request (SSE mode)",
)
async def inject_permission(
    req: PermissionRequest,
    x_session_id: str = Header(..., alias="X-Session-Id"),
):
    """
    当 SSE 流中出现 `{"type": "permission_request", "tool": "...", "input": {...}}` 事件时，
    说明 agent 正在等待用户决定是否允许该工具调用。

    客户端应：
      1. 展示工具名称和输入参数给用户
      2. 让用户选择批准或拒绝
      3. 调用此接口，granted=true 表示批准，granted=false 表示拒绝

    请求体：{ "granted": true }
    Header：X-Session-Id 必须与正在等待的 SSE 会话一致
    """
    emitter = _sse_emitters.get(x_session_id)
    if emitter is None:
        raise HTTPException(
            status_code=404,
            detail=f"No active SSE session for '{x_session_id}', or agent is not waiting for permission.",
        )
    emitter.inject_permission(req.granted)
    return {"status": "ok"}


# ─── Chat: plain HTTP (non-streaming) ─────────────────────────────────────────


@app.post(
    "/chat",
    summary="Send a message and wait for the complete response",
)
async def chat(req: ChatRequest, x_session_id: Optional[str] = Header(None, alias="X-Session-Id")):
    """
    Blocks until the agent finishes. Returns the full reply and an event log.
    Suitable for simple clients that don't need streaming.

    session_id 通过 X-Session-Id header 传入，不传则自动创建新 session。
    每次请求服务端自动生成 conversation_id。

    dag=True: runs through a Pipeline instead of single AgentRunner.
              The pipeline class must be registered in _PIPELINE_REGISTRY.
    """
    session, conversation_id = _get_or_create_session(x_session_id)
    raw_emitter = CollectEmitter()
    emitter = _wrap_emitter(raw_emitter, verbosity=req.verbosity, stream=req.stream, interactive=req.interactive)

    if req.dag:
        pipeline_cls = _get_pipeline_class(req.pipeline_class)
        pipeline = pipeline_cls(session_manager=session_manager)
        await pipeline.run({"message": req.message}, emitter=emitter)
        return {
            "session_id": session.id,
            "conversation_id": conversation_id,
            "reply": raw_emitter.get_final_text(),
            "events": raw_emitter.events,
        }

    await runner.run(session, req.message, emitter)
    return {
        "session_id": session.id,
        "conversation_id": conversation_id,
        "reply": raw_emitter.get_final_text(),
        "events": raw_emitter.events,
    }


# ─── Chat: SSE (streaming) ────────────────────────────────────────────────────


@app.post(
    "/chat/stream",
    summary="Send a message and receive a stream of Server-Sent Events",
)
async def chat_sse(req: ChatRequest, x_session_id: Optional[str] = Header(None, alias="X-Session-Id")):
    """
    Returns a `text/event-stream` response.
    Each line is `data: <json>\n\n` where json has a `type` field:
      token        — partial LLM output
      tool_start   — agent is calling a tool
      tool_result  — tool finished
      compact      — context was compressed
      done         — final complete text（含 session_id 和 conversation_id）
      error        — something went wrong

    session_id 通过 X-Session-Id header 传入，不传则自动创建新 session。
    dag=True: runs through a Pipeline (pipeline_class must be registered).
    """
    session, conversation_id = _get_or_create_session(x_session_id)
    raw_emitter = SSEEmitter()
    emitter = _wrap_emitter(raw_emitter, verbosity=req.verbosity, stream=req.stream, interactive=req.interactive)

    # 注册 emitter，允许 /chat/stream/answer 端点注入 AskUserQuestion 的答案
    _sse_emitters[session.id] = raw_emitter

    if req.dag:
        pipeline_cls = _get_pipeline_class(req.pipeline_class)
        pipeline = pipeline_cls(session_manager=session_manager)

        async def _run_dag():
            try:
                await pipeline.run({"message": req.message}, emitter=emitter)
            finally:
                await raw_emitter.close()
                _sse_emitters.pop(session.id, None)

        asyncio.create_task(_run_dag())
    else:
        async def _run():
            try:
                await runner.run(session, req.message, emitter)
            finally:
                await raw_emitter.close()
                _sse_emitters.pop(session.id, None)

        asyncio.create_task(_run())

    async def _generate() -> AsyncIterator[str]:
        async for data in raw_emitter.event_stream():
            event = json.loads(data)
            # done 事件附上 session_id 和 conversation_id，方便客户端保存
            if event.get("type") == "done":
                event["session_id"] = session.id
                event["conversation_id"] = conversation_id
                yield f"data: {json.dumps(event)}\n\n"
            else:
                yield f"data: {data}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


# ─── Chat: WebSocket ──────────────────────────────────────────────────────────


@app.websocket("/chat/ws")
async def chat_ws(websocket: WebSocket):
    """
    WebSocket endpoint.

    第一条消息必须是握手包，用于绑定 session：
      { "session_id": "<id>" }         — 使用已有 session
      { "session_id": null }           — 自动创建新 session（也可省略该字段）

    后续消息格式：
      { "message": "your prompt" }

    服务端推送与 SSE 相同的事件格式（token, tool_start, done 等）。
    done 事件包含 session_id 和 conversation_id。
    """
    await websocket.accept()
    emitter = WSEmitter(websocket)

    # 第一条消息：握手，确定 session
    try:
        raw = await websocket.receive_text()
        handshake = json.loads(raw)
        session_id = handshake.get("session_id")
    except (json.JSONDecodeError, Exception):
        await websocket.close(code=4000, reason="Invalid handshake")
        return

    if session_id:
        session = session_manager.get(session_id)
        if not session:
            await websocket.close(code=4004, reason="Session not found")
            return
    else:
        session = session_manager.create()

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
                message = payload.get("message", raw)
                verbosity = payload.get("verbosity", "verbose")
                stream = bool(payload.get("stream", True))
                interactive = bool(payload.get("interactive", True))
            except json.JSONDecodeError:
                message = raw
                verbosity = "verbose"
                stream = True
                interactive = True

            conversation_id = str(uuid.uuid4())
            _storage.create_conversation(session.id, conversation_id)

            turn_emitter = _wrap_emitter(emitter, verbosity=verbosity, stream=stream, interactive=interactive)
            await runner.run(session, message, turn_emitter)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await emitter.emit_error(str(e))
        except Exception:
            pass


# ─── Helpers ──────────────────────────────────────────────────────────────────

# Pipeline 注册表：pipeline_class 参数值 → Pipeline 子类
# 用户可在此处注册自己的 Pipeline 实现，例如：
#   from my_pipelines import CodeReviewPipeline
#   _PIPELINE_REGISTRY["code_review"] = CodeReviewPipeline
_PIPELINE_REGISTRY: dict[str, type[Pipeline]] = {}



def _get_pipeline_class(name: str | None) -> type[Pipeline]:
    if not name:
        raise HTTPException(
            status_code=400,
            detail="dag=True 时必须提供 pipeline_class 参数",
        )
    cls = _PIPELINE_REGISTRY.get(name)
    if cls is None:
        raise HTTPException(
            status_code=400,
            detail=f"未找到 Pipeline: {name!r}。已注册: {list(_PIPELINE_REGISTRY.keys())}",
        )
    return cls


def _get_or_404(session_id: str) -> Session:
    session = session_manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return session


def _get_or_create_session(session_id: Optional[str]) -> tuple[Session, str]:
    """
    按 session_id 查找 session，不存在或不传则新建。
    同时创建本次请求的 conversation_id，注册到 SQLite adapter。
    返回 (session, conversation_id)。
    """
    if session_id:
        session = session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    else:
        session = session_manager.create()

    conversation_id = str(uuid.uuid4())
    _storage.create_conversation(session.id, conversation_id)

    return session, conversation_id


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    # log_config=None：禁止 uvicorn 用 dictConfig 覆盖我们已装好的 loguru handler
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False, log_config=None)
