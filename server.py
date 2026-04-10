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
from ccserver.core.emitter.api_emitters import SSEEmitter, WSEmitter, CollectEmitter
from ccserver.core.emitter.filter_emitter import FilterEmitter, VALID_MODES as _OUTPUT_MODES
from loguru import logger
from ccserver.log import setup_logging
from contextlib import asynccontextmanager

setup_logging(stderr=True)

import os
logger.info("server startup env | CONVERSATION_ID={}", os.environ.get("CONVERSATION_ID", "<missing>"))

# ─── App setup ────────────────────────────────────────────────────────────────

_storage = build_storage(
    STORAGE_BACKEND, SESSIONS_BASE, DB_PATH,
    mongo_uri=MONGO_URI, mongo_db=MONGO_DB,
    redis_url=REDIS_URL, redis_cache_size=REDIS_CACHE_SIZE, redis_ttl=REDIS_TTL,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup：MongoDB 连通性检查（仅 mongo backend）
    if hasattr(_storage, "ping"):
        await _storage.ping()
    yield
    # shutdown：关闭连接
    if hasattr(_storage, "close"):
        await _storage.close()


app = FastAPI(title="CCServer", version="1.0.0", lifespan=lifespan)
session_manager = SessionManager(SESSIONS_BASE, storage=_storage)

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


def _wrap_emitter(inner, mode: str | None):
    """根据 output_mode 决定是否用 FilterEmitter 包装。None 或 interactive 直接返回原 emitter。"""
    if mode and mode in _OUTPUT_MODES and mode != "interactive":
        return FilterEmitter(inner, mode=mode)
    return inner


# ─── Request / Response schemas ───────────────────────────────────────────────


class CreateSessionRequest(BaseModel):
    session_id: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    dag: bool = False                  # True = Pipeline 模式，False = 单 Agent 模式（默认）
    pipeline_class: str | None = None  # dag=True 时指定 pipeline 类名（预留，暂未使用）
    output_mode: str | None = None     # 根代理输出模式：interactive/final_only/streaming/verbose
    run_mode: str | None = None        # 运行模式："auto"（全自动）或 "interactive"（等待用户确认）；None = 读 settings


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
    emitter = _wrap_emitter(raw_emitter, req.output_mode)

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

    await runner.run(session, req.message, emitter, run_mode=req.run_mode)
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
    emitter = _wrap_emitter(raw_emitter, req.output_mode)

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
                await runner.run(session, req.message, emitter, run_mode=req.run_mode)
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
                output_mode = payload.get("output_mode")
            except json.JSONDecodeError:
                message = raw
                output_mode = None

            conversation_id = str(uuid.uuid4())
            _storage.create_conversation(session.id, conversation_id)

            turn_emitter = _wrap_emitter(emitter, output_mode)
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

    print(
        "\033[36m"
        "\n  ██████╗ ██████╗███████╗███████╗██████╗ ██╗   ██╗███████╗██████╗ "
        "\n  ██╔════╝██╔════╝██╔════╝██╔════╝██╔══██╗██║   ██║██╔════╝██╔══██╗"
        "\n  ██║     ██║     ███████╗█████╗  ██████╔╝██║   ██║█████╗  ██████╔╝"
        "\n  ██║     ██║     ╚════██║██╔══╝  ██╔══██╗╚██╗ ██╔╝██╔══╝  ██╔══██╗"
        "\n  ╚██████╗╚██████╗███████║███████╗██║  ██║ ╚████╔╝ ███████╗██║  ██║"
        "\n   ╚═════╝ ╚═════╝╚══════╝╚══════╝╚═╝  ╚═╝  ╚═══╝  ╚══════╝╚═╝  ╚═╝"
        "\033[0m"
        f"\n\033[2m  API Server — http://0.0.0.0:8000  |  project: {_project_dir}\033[0m\n"
    )

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
