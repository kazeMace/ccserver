#!/usr/bin/env python3
"""
CCServer — FastAPI with SSE, WebSocket, and plain HTTP endpoints.
"""

import os
import sys
from dotenv import load_dotenv
load_dotenv()

# ── 启动时校验 PROJECT_DIR（必须在业务导入之前）────────────────────────────────
from pathlib import Path

_project_dir_env = os.getenv("CCSERVER_PROJECT_DIR")
if _project_dir_env:
    _project_dir = Path(_project_dir_env).resolve()
    if not _project_dir.exists():
        print(
            f"[ERROR] CCSERVER_PROJECT_DIR='{_project_dir}' does not exist.",
            file=sys.stderr,
        )
        sys.exit(1)
else:
    print(
        "[WARN] CCSERVER_PROJECT_DIR is not set. "
        "Sessions will use temporary directories under /tmp/ as project_root. "
        "All project-level configs (CLAUDE.md, skills, agents, hooks, commands, MCP) will be empty.",
        file=sys.stderr,
    )
    _project_dir = None

# ── 业务导入 ────────────────────────────────────────────────────────────────────
import asyncio
import json
import uuid
from datetime import datetime, timezone
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
    DB_PATH, INJECT_SYSTEM_FILE, APPEND_SYSTEM, STORAGE_BACKEND,
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
from ccserver.monitor import MonitorCollector, get_monitor_html
from loguru import logger
from ccserver.log import setup_logging
from contextlib import asynccontextmanager
import time as _time

# ── Channel 系统 ─────────────────────────────────────────────────────────────
from ccserver.channels import ChannelGateway, ChannelRegistry
from ccserver.channels.config import ChannelConfig
from ccserver.outbound_bus import OutboundBus

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
    _proj_display = _project_dir if _project_dir else "(temporary, no project dir set)"
    print(f"\n\033[2m  API Server — http://0.0.0.0:8000  |  project: {_proj_display}\033[0m\n")

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
    # 初始化内置 Agent 系统（加载 agents.json + 扫描 specs/ 包）
    _init_builtin_agents()
    # 自动扫描适配器并启动配置中 enabled + auto_start 的 channel
    # 放到后台协程执行，避免阻塞 uvicorn startup（如 discord 连接超时 30s）
    gateway = _get_channel_gateway()

    async def _start_channels_async():
        auto_result = await gateway.auto_discover_and_start()
        logger.info(
            "Channel auto-start | discovered={} started={} failed={} skipped={}",
            auto_result["discovered"],
            auto_result["started"],
            auto_result["failed"],
            auto_result["skipped"],
        )

    asyncio.create_task(_start_channels_async())
    yield
    # shutdown：ChannelGateway 关闭 → Team 监控停止 → 存储关闭 → HTTP 客户端关闭
    if _channel_gateway is not None:
        await _channel_gateway.shutdown()
    _team_monitor.stop()
    if hasattr(_storage, "close"):
        await _storage.close()
    # 关闭 WebFetch / DuckDuckGo 共享的 HTTP 客户端，释放 TCP 连接池
    from ccserver.builtins.tools.web_fetch import close_http_client as _close_webfetch_client
    from ccserver.builtins.tools.duckduckgo_search import close_http_client as _close_ddg_client
    await _close_webfetch_client()
    await _close_ddg_client()


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

# ── Channel 系统初始化 ────────────────────────────────────────────────────────
_channel_registry = ChannelRegistry()
_outbound_bus = OutboundBus()
_channel_gateway: ChannelGateway | None = None  # 延迟初始化


def _init_builtin_agents() -> int:
    """
    初始化内置 Agent 系统。

    加载 agents.json 配置，扫描 ccserver.builtins.agents.specs 包，
    自动发现并注册所有 BaseAgentSpec 子类。

    在 server lifespan startup 时调用，确保内置 Agent 在 AgentLoader
    初始化之前完成注册。

    Returns:
        注册的内置 Agent 数量
    """
    try:
        from ccserver.builtins.agents.config import agent_config
        from ccserver.builtins.agents.registry import agent_registry

        # 1. 加载 agents.json 配置
        cfg = agent_config()
        cfg.load()

        # 2. 自动扫描 specs/ 包
        reg = agent_registry()
        count = reg.discover()
        logger.info(
            "Builtin agents initialized | count={} names={}",
            count, reg.list_names(),
        )
        return count
    except Exception as e:
        logger.warning("Builtin agents initialization failed | err={}", e)
        return 0


def _get_channel_gateway() -> ChannelGateway:
    """
    获取或创建 ChannelGateway。

    ChannelGateway 依赖 runner，runner 在下面创建，
    所以这里用延迟初始化模式。

    适配器通过 registry.discover() 自动扫描注册，
    无需在 server.py 中手动 import 和 register。
    """
    global _channel_gateway
    if _channel_gateway is None:
        _channel_gateway = ChannelGateway(
            registry=_channel_registry,
            session_manager=session_manager,
            runner=runner,
            outbound_bus=_outbound_bus,
            config=ChannelConfig(),
        )
        # 自动扫描 adapters 目录下的所有适配器
        discovered = _channel_registry.discover()
        logger.info(
            "ChannelGateway initialized | discovered={} channels={}",
            discovered,
            list(_channel_registry._adapters.keys()),
        )
    return _channel_gateway

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
        raise FileNotFoundError(f"CCSERVER_INJECT_SYSTEM_FILE 不存在: {path}")
    return p.read_text(encoding="utf-8")


runner = AgentRunner(system=_read_system_file(INJECT_SYSTEM_FILE), append_system=APPEND_SYSTEM)


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


class CreateScheduledTaskRequest(BaseModel):
    prompt: str
    schedule: str = ""                      # 自然语言描述（优先）
    trigger_type: str = ""                  # cron / interval / countdown / once
    cron: str = ""                          # 5 字段 cron 表达式
    interval_seconds: int = 0
    run_at: str = ""                        # ISO datetime
    durable: bool = False
    max_triggers: int = 0                   # 0 = unlimited
    end_time: str = ""                      # ISO datetime


class UpdateScheduledTaskRequest(BaseModel):
    prompt: str = ""
    enabled: bool = True
    max_triggers: int = -1                  # -1 = no change
    end_time: str = ""
    cron: str = ""
    interval_seconds: int = 0


# ─── Session routes ───────────────────────────────────────────────────────────


@app.post("/sessions", summary="Create a new session with an isolated workdir")
def create_session(req: CreateSessionRequest) -> dict:
    session = session_manager.create(req.session_id)
    return session.to_meta()


@app.get("/sessions", summary="List all sessions")
def list_sessions() -> list[dict]:
    return session_manager.list_all()


@app.get("/sessions/{session_id}", summary="Get session metadata")
def get_session(session_id: str) -> dict:
    session = _get_or_404(session_id)
    return {**session.to_meta(), "message_count": len(session.messages)}


# ─── Shell 后台任务查询 ─────────────────────────────────────────────────────────


@app.get(
    "/sessions/{session_id}/tasks",
    summary="List all shell background tasks for a session",
)
def list_shell_tasks(session_id: str) -> list[dict]:
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
def kill_shell_task(session_id: str, task_id: str) -> dict:
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
def get_shell_task(session_id: str, task_id: str) -> dict:
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


# ─── Scheduled Task REST API ──────────────────────────────────────────────────


def _get_cron_scheduler(session_id: str):
    """辅助函数：获取 session 的 cron_scheduler，若不存在返回 404。"""
    session = _get_or_404(session_id)
    scheduler = getattr(session, "cron_scheduler", None)
    if scheduler is None:
        raise HTTPException(
            status_code=404,
            detail="Scheduled task scheduler not available for this session.",
        )
    return scheduler


@app.post(
    "/sessions/{session_id}/scheduled-tasks",
    summary="Create a scheduled task",
)
def create_scheduled_task(session_id: str, req: CreateScheduledTaskRequest) -> dict:
    """
    创建定时任务。支持自然语言或显式参数。

    自然语言示例：
        { "prompt": "check port 8000", "schedule": "every 30 seconds" }
    显式参数示例：
        { "prompt": "check port 8000", "trigger_type": "interval", "interval_seconds": 30 }
    """
    scheduler = _get_cron_scheduler(session_id)

    # 优先使用自然语言解析
    from ccserver.managers.cron import parse_natural_language_schedule, ScheduleSpec

    spec: ScheduleSpec | None = None
    if req.schedule:
        spec = parse_natural_language_schedule(req.schedule)
        if spec is None:
            raise HTTPException(
                status_code=400,
                detail=f"Could not parse schedule: {req.schedule!r}",
            )

    trigger_type = spec.trigger_type if spec else req.trigger_type
    cron_expr = spec.cron_expr if spec else req.cron
    interval_seconds = spec.interval_seconds if spec else req.interval_seconds
    run_at = spec.run_at if spec else None
    max_triggers = spec.max_triggers if (spec and spec.max_triggers is not None) else (
        req.max_triggers if req.max_triggers > 0 else None
    )
    end_time = spec.end_time if (spec and spec.end_time is not None) else None

    # 解析 run_at 字符串
    if req.run_at and not run_at:
        try:
            run_at = datetime.fromisoformat(req.run_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid run_at: {req.run_at!r}")

    # 解析 end_time 字符串
    if req.end_time and not end_time:
        try:
            end_time = datetime.fromisoformat(req.end_time.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid end_time: {req.end_time!r}")

    # 如果没有 trigger_type，根据参数推断
    if not trigger_type:
        if req.cron:
            trigger_type = "cron"
        elif req.interval_seconds > 0:
            trigger_type = "interval"
        elif req.run_at:
            trigger_type = "once"
        else:
            raise HTTPException(
                status_code=400,
                detail="Either 'schedule' or 'trigger_type' + schedule fields must be provided.",
            )

    try:
        task = scheduler.create(
            prompt=req.prompt,
            trigger_type=trigger_type,  # type: ignore[arg-type]
            cron_expr=cron_expr,
            interval_seconds=interval_seconds,
            run_at=run_at,
            durable=req.durable,
            max_triggers=max_triggers,
            end_time=end_time,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "task_id": task.task_id,
        "trigger_type": task.trigger_type,
        "next_run_at": task.next_run_at.isoformat() if task.next_run_at else None,
        "enabled": task.enabled,
        "status": task.status,
    }


@app.get(
    "/sessions/{session_id}/scheduled-tasks",
    summary="List all scheduled tasks for a session",
)
def list_scheduled_tasks(session_id: str) -> dict:
    """返回当前 Session 中所有定时任务列表。"""
    scheduler = _get_cron_scheduler(session_id)
    tasks = scheduler.list_all()
    return {
        "tasks": [
            {
                "task_id": t.task_id,
                "trigger_type": t.trigger_type,
                "prompt": t.prompt[:100] + "..." if len(t.prompt) > 100 else t.prompt,
                "next_run_at": t.next_run_at.isoformat() if t.next_run_at else None,
                "enabled": t.enabled,
                "status": t.status,
                "trigger_count": t.trigger_count,
                "max_triggers": t.max_triggers,
                "end_time": t.end_time.isoformat() if t.end_time else None,
                "durable": t.durable,
            }
            for t in tasks
        ],
        "count": len(tasks),
    }


@app.get(
    "/sessions/{session_id}/scheduled-tasks/{task_id}",
    summary="Get a scheduled task by ID",
)
def get_scheduled_task(session_id: str, task_id: str) -> dict:
    """根据 task_id 获取单个定时任务详情。"""
    scheduler = _get_cron_scheduler(session_id)
    task = scheduler.get(task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail=f"Task '{task_id}' not found in session '{session_id}'.",
        )
    return {
        "task_id": task.task_id,
        "trigger_type": task.trigger_type,
        "prompt": task.prompt,
        "cron_expr": task.cron_expr,
        "interval_seconds": task.interval_seconds,
        "run_at": task.run_at.isoformat() if task.run_at else None,
        "next_run_at": task.next_run_at.isoformat() if task.next_run_at else None,
        "enabled": task.enabled,
        "status": task.status,
        "trigger_count": task.trigger_count,
        "max_triggers": task.max_triggers,
        "end_time": task.end_time.isoformat() if task.end_time else None,
        "durable": task.durable,
        "jitter_max": task.jitter_max,
        "created_at": task.created_at.isoformat(),
        "last_triggered_at": task.last_triggered_at.isoformat() if task.last_triggered_at else None,
    }


@app.put(
    "/sessions/{session_id}/scheduled-tasks/{task_id}",
    summary="Update a scheduled task",
)
def update_scheduled_task(
    session_id: str, task_id: str, req: UpdateScheduledTaskRequest
) -> dict:
    """更新现有定时任务的配置。只修改提供的字段。"""
    scheduler = _get_cron_scheduler(session_id)

    if scheduler.get(task_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"Task '{task_id}' not found in session '{session_id}'.",
        )

    kwargs: dict = {}
    if req.prompt:
        kwargs["prompt"] = req.prompt
    if req.enabled is not None:
        kwargs["enabled"] = req.enabled
    if req.max_triggers >= 0:
        kwargs["max_triggers"] = req.max_triggers if req.max_triggers > 0 else None
    if req.end_time:
        try:
            kwargs["end_time"] = datetime.fromisoformat(
                req.end_time.replace("Z", "+00:00")
            )
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Invalid end_time: {req.end_time!r}"
            )
    if req.cron:
        kwargs["cron_expr"] = req.cron
    if req.interval_seconds > 0:
        kwargs["interval_seconds"] = req.interval_seconds

    try:
        task = scheduler.update(task_id, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "task_id": task.task_id,
        "trigger_type": task.trigger_type,
        "next_run_at": task.next_run_at.isoformat() if task.next_run_at else None,
        "enabled": task.enabled,
        "status": task.status,
    }


@app.delete(
    "/sessions/{session_id}/scheduled-tasks/{task_id}",
    summary="Delete a scheduled task",
)
def delete_scheduled_task(session_id: str, task_id: str) -> dict:
    """删除指定定时任务。"""
    scheduler = _get_cron_scheduler(session_id)
    deleted = scheduler.delete(task_id)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Task '{task_id}' not found in session '{session_id}'.",
        )
    return {"status": "ok", "message": f"Task '{task_id}' deleted."}


@app.post(
    "/sessions/{session_id}/scheduled-tasks/{task_id}/toggle",
    summary="Toggle a scheduled task enabled/disabled",
)
def toggle_scheduled_task(session_id: str, task_id: str) -> dict:
    """启用/禁用切换。"""
    scheduler = _get_cron_scheduler(session_id)
    found, new_enabled = scheduler.toggle(task_id)
    if not found:
        raise HTTPException(
            status_code=404,
            detail=f"Task '{task_id}' not found in session '{session_id}'.",
        )
    return {
        "status": "ok",
        "task_id": task_id,
        "enabled": new_enabled,
    }


# ─── Agent 后台任务查询 ────────────────────────────────────────────────────────


@app.get(
    "/sessions/{session_id}/agent-tasks",
    summary="List all agent background tasks for a session",
)
def list_agent_tasks(session_id: str) -> list[dict]:
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
def get_agent_task(session_id: str, task_id: str) -> dict:
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
async def cancel_agent_task(session_id: str, task_id: str) -> dict:
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
def list_teams(session_id: str) -> list[dict]:
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
def create_team(session_id: str, req: CreateTeamRequest) -> dict:
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
def get_team(session_id: str, team_name: str) -> dict:
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
def delete_team(session_id: str, team_name: str) -> dict:
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
def list_members(session_id: str, team_name: str) -> list[dict]:
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
def add_member(session_id: str, team_name: str, req: AddMemberRequest) -> dict:
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
def remove_member(session_id: str, team_name: str, agent_id: str) -> dict:
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
def team_health(session_id: str, team_name: str) -> dict:
    """
    返回团队后台组件的健康状态：
      - dispatcher_alive: TeamTaskDispatcher 是否在运行
      - pollers_alive: 各 MailboxPoller 是否在运行
    """
    session = _get_or_404(session_id)
    team = _get_team_or_404(session, team_name)

    dispatcher = getattr(team, "_dispatcher", None)

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
        "pollers_alive": pollers,
    }


# ─── Mailbox routes ───────────────────────────────────────────────────────────


@app.post(
    "/sessions/{session_id}/teams/{team_name}/mailbox/send",
    summary="Send a mailbox message to a teammate",
)
async def send_mailbox_message(
    session_id: str,
    team_name: str,
    req: SendTeamMessageRequest,
) -> dict:
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
        await mailbox.broadcast(chat_msg, recipients=recipients)
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
        await mailbox.send(chat_msg)
        return {"status": "ok", "to": to_agent}


@app.get(
    "/sessions/{session_id}/teams/{team_name}/mailbox/{agent_id}",
    summary="Fetch mailbox messages for a teammate",
)
async def fetch_mailbox_messages(
    session_id: str,
    team_name: str,
    agent_id: str,
    unread_only: bool = True,
    limit: int = 100,
) -> list[dict]:
    """获取指定成员的 Mailbox 消息列表。"""
    session = _get_or_404(session_id)
    _get_team_or_404(session, team_name)
    mailbox = TeamMailbox(team_name, session.storage)
    msgs = await mailbox.fetch_messages(agent_id, unread_only=unread_only, limit=limit)
    return {
        "messages": [m.to_dict() for m in msgs],
        "count": len(msgs),
    }


@app.post(
    "/sessions/{session_id}/teams/{team_name}/mailbox/{agent_id}/read",
    summary="Mark mailbox messages as read",
)
async def mark_mailbox_read(
    session_id: str,
    team_name: str,
    agent_id: str,
    req: MarkReadRequest,
) -> dict:
    """将指定消息标记为已读。"""
    session = _get_or_404(session_id)
    _get_team_or_404(session, team_name)
    mailbox = TeamMailbox(team_name, session.storage)
    await mailbox.mark_read(agent_id, req.msg_ids)
    return {"status": "ok", "marked_count": len(req.msg_ids)}


# ─── Channel 管理 API ─────────────────────────────────────────────────────────


class StartChannelRequest(BaseModel):
    channel_id: str
    account_id: str = "default"
    config: dict = {}


@app.get("/channels", summary="List all registered channel adapters")
def list_channels() -> list[dict]:
    """
    返回所有已注册的 channel 适配器列表及其能力声明。

    Returns:
        channels: list[{
            "id": "webchat",
            "aliases": ["web", "browser"],
            "capabilities": { ... }
        }]
    """
    return {"channels": _channel_registry.list_channels()}


@app.get("/channels/status", summary="List running channel accounts")
def list_channel_status() -> list[dict]:
    """
    返回所有正在运行的 channel 账户状态。

    Returns:
        channels: list[{
            "channel_id": "webchat",
            "account_id": "default",
            "status": { ... }
        }]
    """
    gateway = _get_channel_gateway()
    return {"channels": gateway.list_running()}


@app.post("/channels/start", summary="Start a channel account")
async def start_channel(req: StartChannelRequest) -> dict:
    """
    启动一个 channel 账户。

    启动后，适配器会主动连接外部平台（建立 WebSocket/Stream 连接），
    并开始接收入站消息。

    Args:
        channel_id: channel ID（如 "webchat", "discord", "feishu"）
        account_id: 账户标识（多账户场景下区分不同 bot）
        config:     平台特定的配置（token、app_id 等）

    Returns:
        启动后的账户状态快照
    """
    gateway = _get_channel_gateway()
    try:
        snapshot = await gateway.start_channel(
            req.channel_id,
            req.account_id,
            req.config,
        )
        return {"status": "started", "snapshot": snapshot.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Channel start failed | channel={} err={}", req.channel_id, e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/channels/{channel_id}/stop", summary="Stop a channel account")
async def stop_channel(channel_id: str, account_id: str = "default") -> dict:
    """
    停止一个 channel 账户。

    关闭与外部平台的连接，不再接收新消息。

    Args:
        channel_id: channel ID
        account_id: 账户标识

    Returns:
        {"status": "stopped"}
    """
    gateway = _get_channel_gateway()
    await gateway.stop_channel(channel_id, account_id)
    return {"status": "stopped"}


@app.get("/channels/{channel_id}/status", summary="Get channel account status")
async def get_channel_status(channel_id: str, account_id: str = "default") -> dict:
    """
    查询 channel 账户的实时状态。

    Args:
        channel_id: channel ID
        account_id: 账户标识

    Returns:
        账户状态快照
    """
    gateway = _get_channel_gateway()
    try:
        snapshot = await gateway.get_status(channel_id, account_id)
        return {"status": snapshot.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ─── Webhook 统一回调 ─────────────────────────────────────────────────────────


from starlette.requests import Request as StarletteRequest


@app.post(
    "/webhook/{channel_id}",
    summary="Channel Webhook 统一回调",
    include_in_schema=False,
)
async def channel_webhook(channel_id: str, request: StarletteRequest) -> dict:
    """
    接收各 channel 适配器的 Webhook 回调。

    通用入口：根据 URL 中的 channel_id 找到对应 adapter，
    调用其 handle_webhook() 方法处理请求。

    支持的适配器：
      - feishu: 飞书事件推送（/webhook/feishu）
      - 其他需要 HTTP 回调的 channel...

    不支持的适配器（走内部事件循环，无需 webhook）：
      - discord：使用 discord.py Gateway WebSocket
      - imessage：轮询本地 SQLite 数据库
      - webchat：通过 SSE/WS HTTP 路由

    注意：
      - 需要公网可访问的地址
      - 本地开发可用 ngrok 转发
      - 飞书等平台要求 5 秒内响应，超时会重试
    """
    # 通过 registry 查找 adapter
    adapter = _channel_registry.get_adapter(channel_id)
    if adapter is None:
        logger.warning(
            "Webhook received for unregistered channel | channel={}",
            channel_id,
        )
        return {"code": 404, "msg": f"Channel '{channel_id}' not found"}

    # 检查 adapter 是否有 handle_webhook 方法
    if not hasattr(adapter, "handle_webhook"):
        logger.warning(
            "Webhook received but adapter has no handle_webhook | channel={}",
            channel_id,
        )
        return {"code": 400, "msg": f"Channel '{channel_id}' does not support webhooks"}

    # 解析 JSON body
    try:
        body = await request.json()
    except Exception:
        body = {}

    # 提取 HTTP headers（各平台签名验证需要）
    webhook_headers = dict(request.headers)

    # 调用 adapter 处理 webhook
    try:
        result = await adapter.handle_webhook(body, webhook_headers)

        if result is not None:
            # 各平台特定响应（如飞书 Challenge 验证）
            return result
        else:
            return {"code": 0}
    except Exception as e:
        logger.error(
            "Webhook handler error | channel={} err={}",
            channel_id, e,
        )
        return {"code": 500, "msg": str(e)}


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
) -> dict:
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
    # P1: SSEEmitter 直接订阅 EventBus，独立接收后台 Agent 事件
    raw_emitter = SSEEmitter(
        session=session,
        event_bus=session.event_bus,
        client_id=conversation_id,
    )
    emitter = _wrap_emitter(raw_emitter, verbosity=req.verbosity, stream=req.stream, interactive=req.interactive)

    # 注册 emitter，允许 /chat/stream/answer 端点注入 AskUserQuestion 的答案
    _sse_emitters[session.id] = raw_emitter

    # 注册到 ChannelGateway（WebChatAdapter），支持多 channel 架构
    gateway = _get_channel_gateway()
    webchat = _channel_registry.get_adapter("webchat")
    if webchat is not None:
        webchat.register_emitter(session.id, raw_emitter, client_info={
            "type": "sse",
            "client_id": conversation_id,
        })

    # EventBus 过滤函数：只接收当前 Session 内的事件
    _event_bus_filter = lambda e: e.session_id == session.id

    # 保存后台任务引用，客户端断开时用于取消
    _agent_task: asyncio.Task | None = None

    if req.dag:
        pipeline_cls = _get_pipeline_class(req.pipeline_class)
        pipeline = pipeline_cls(session_manager=session_manager)

        async def _run_dag():
            try:
                await pipeline.run({"message": req.message}, emitter=emitter)
            finally:
                await raw_emitter.close()
                _sse_emitters.pop(session.id, None)
                # 注销 WebChatAdapter
                webchat = _channel_registry.get_adapter("webchat")
                if webchat is not None:
                    webchat.unregister_session(session.id)
                # 清理 ChannelGateway 路由
                if _channel_gateway is not None:
                    await _channel_gateway.cleanup_session(session.id)

        _agent_task = asyncio.create_task(_run_dag())
    else:
        async def _run():
            try:
                await runner.run(session, req.message, emitter)
            finally:
                await raw_emitter.close()
                _sse_emitters.pop(session.id, None)
                # 注销 WebChatAdapter
                webchat = _channel_registry.get_adapter("webchat")
                if webchat is not None:
                    webchat.unregister_session(session.id)
                # 清理 ChannelGateway 路由
                if _channel_gateway is not None:
                    await _channel_gateway.cleanup_session(session.id)

        _agent_task = asyncio.create_task(_run())

    # 启动 EventBus 订阅，使 SSE 客户端能直接收到后台 Agent / teammate 的事件
    await raw_emitter.start_event_bus_subscription(_event_bus_filter)

    async def _generate() -> AsyncIterator[str]:
        try:
            async for data in raw_emitter.event_stream():
                event = json.loads(data)
                # done 事件附上 session_id 和 conversation_id，方便客户端保存
                if event.get("type") == "done":
                    event["session_id"] = session.id
                    event["conversation_id"] = conversation_id
                    yield f"data: {json.dumps(event)}\n\n"
                else:
                    yield f"data: {data}\n\n"
        finally:
            # 客户端断开 SSE 连接时，取消后台 agent 任务，触发 finally 清理
            if _agent_task is not None and not _agent_task.done():
                _agent_task.cancel()
                try:
                    await _agent_task
                except asyncio.CancelledError:
                    pass

    return StreamingResponse(_generate(), media_type="text/event-stream")


# ─── Chat: WebSocket ──────────────────────────────────────────────────────────


@app.websocket("/chat/ws")
async def chat_ws(websocket: WebSocket) -> None:
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

    # P1: WSEmitter 直接订阅 EventBus，独立接收后台 Agent 事件
    emitter = WSEmitter(
        websocket=websocket,
        session=session,
        event_bus=session.event_bus,
        client_id=session.id,
    )
    _event_bus_filter = lambda e: e.session_id == session.id
    await emitter.start_event_bus_subscription(_event_bus_filter)

    # 注册到 ChannelGateway（WebChatAdapter）
    webchat = _channel_registry.get_adapter("webchat")
    if webchat is not None:
        webchat.register_emitter(session.id, emitter, client_info={
            "type": "websocket",
            "remote": str(websocket.client) if websocket.client else None,
        })

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
    finally:
        # 客户端断开时停止 EventBus 订阅，防止泄漏
        await emitter.stop_event_bus_subscription()
        # 注销 WebChatAdapter
        webchat = _channel_registry.get_adapter("webchat")
        if webchat is not None:
            webchat.unregister_session(session.id)
        # 清理 ChannelGateway 路由
        if _channel_gateway is not None:
            await _channel_gateway.cleanup_session(session.id)


# ─── Monitor Dashboard ─────────────────────────────────────────────────────────


@app.get("/monitor", summary="Monitor dashboard HTML page")
def monitor_page() -> str:
    """
    返回监控 Dashboard 的 HTML 页面。

    阶段1：纯 HTML + JS，零前端构建工具。
    阶段2（未来）：迁移到 React 后，本路由将指向构建产物。
    """
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=get_monitor_html())


@app.websocket("/monitor/ws")
async def monitor_ws(websocket: WebSocket) -> None:
    """
    监控 Dashboard 的 WebSocket 端点。

    连接建立后，自动订阅所有 Session 的 EventBus，
    将事件实时推送给前端。同时定期推送全局状态快照。

    前端通过单个 WebSocket 连接即可监控整个服务器的运行状态。
    """
    await websocket.accept()
    logger.info("Monitor WebSocket connected | client={}", websocket.client)

    collector = MonitorCollector(session_manager=session_manager, websocket=websocket)

    try:
        await collector.start()
        # 保持连接存活，直到客户端断开
        while True:
            # 接收前端消息（目前只处理 ping，用于保持连接）
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                # 前端可以发送 ping 或控制命令
                try:
                    msg = json.loads(raw)
                    if msg.get("action") == "ping":
                        await websocket.send_json({"type": "pong"})
                except json.JSONDecodeError:
                    pass
            except asyncio.TimeoutError:
                # 超时后发送 keepalive ping
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break
    except Exception as e:
        logger.debug("Monitor WebSocket error | error={}", e)
    finally:
        await collector.stop()
        logger.info("Monitor WebSocket disconnected")


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
    import argparse

    # 命令行参数解析
    parser = argparse.ArgumentParser(description="CCServer API")
    parser.add_argument("--monitor", action="store_true", help="启动后自动打开监控页面")
    parser.add_argument("--port", type=int, default=8000, help="服务端口（默认 8000）")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="绑定地址（默认 0.0.0.0）")
    # 解析已知的参数，忽略 uvicorn 不认识的参数
    args, _ = parser.parse_known_args()

    # 自动打开浏览器（如果传了 --monitor）
    if args.monitor:
        import threading
        import webbrowser

        def _open_browser():
            """延迟 1.5 秒后打开监控页面，等待 uvicorn 启动完成。"""
            import time
            time.sleep(1.5)
            url = f"http://{ '127.0.0.1' if args.host == '0.0.0.0' else args.host }:{args.port}/monitor"
            webbrowser.open(url)
            logger.info("Monitor page opened | url={}", url)

        threading.Thread(target=_open_browser, daemon=True).start()
        logger.info("Auto-open monitor enabled | url will open in 1.5s")

    # ── uvicorn WebSocket DEBUG 日志过滤 ──────────────────────────────────────
    # 环境变量 CCSERVER_UVICORN_WS_LOG=0 时，过滤掉 WebSocket 流量/PING/PONG 等
    # 高频 DEBUG 消息，保留其他 uvicorn 日志（启动、关闭、连接建立等）
    import logging
    if os.environ.get("CCSERVER_UVICORN_WS_LOG", "1") == "0":
        class _UvicornWSFilter(logging.Filter):
            """拦截 uvicorn WebSocket 协议层面的高频 DEBUG 消息。"""
            _patterns = (
                "> TEXT", "> PING", "< PONG",
                "sending keepalive", "received keepalive",
            )
            def filter(self, record):
                msg = record.getMessage()
                return not any(p in msg for p in self._patterns)
        logging.getLogger("uvicorn.error").addFilter(_UvicornWSFilter())
        logger.debug("Uvicorn WebSocket DEBUG log filtered | CCSERVER_UVICORN_WS_LOG=0")

    # log_config=None：禁止 uvicorn 用 dictConfig 覆盖我们已装好的 loguru handler
    uvicorn.run("server:app", host=args.host, port=args.port, reload=False, log_config=None)
