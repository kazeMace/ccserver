"""
server — FastAPI HTTP 接口。

接口：
    POST /sessions       创建 session
    POST /chat           发送消息，获取角色回复
    GET  /personas       列出可用人设
"""

import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from graph import PersonaChatGraph, create_graph, init_session, session_exists, list_personas
from ccserver import SessionManager

_session_manager = SessionManager()
_graph: PersonaChatGraph | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _graph
    _graph = await create_graph(_session_manager)
    yield
    # 应用关闭时断开 MCP 连接
    if _graph and _graph._mcp:
        await _graph._mcp.close_all()


app = FastAPI(title="HumanLikeChat", version="2.0.0", lifespan=lifespan)


class SessionRequest(BaseModel):
    persona_id: str


class ChatRequest(BaseModel):
    message: str
    persona_id: str


@app.post("/sessions", summary="创建新 session")
def create_session(req: SessionRequest):
    session_id = str(uuid.uuid4())
    init_session(session_id, req.persona_id)
    return {"session_id": session_id}


@app.get("/personas", summary="列出所有可用人设")
def get_personas():
    return {"personas": list_personas()}


@app.post("/chat", summary="发送消息，获取角色回复")
async def chat(
    req: ChatRequest,
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
):
    """
    Header:
        X-Session-Id: 通过 POST /sessions 获取，不传则自动创建

    Body:
        message:    用户消息内容
        persona_id: 人设名称（通过 GET /personas 获取列表）
    """
    if x_session_id:
        if not session_exists(x_session_id):
            raise HTTPException(status_code=404, detail=f"Session '{x_session_id}' not found")
        session_id = x_session_id
    else:
        session_id = str(uuid.uuid4())
        init_session(session_id, req.persona_id)

    try:
        reply = await _graph.chat(
            user_input=req.message,
            persona_id=req.persona_id,
            session_id=session_id,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {"session_id": session_id, "reply": reply}
