"""
graph — PersonaChatGraph 及相关业务逻辑。

节点定义见 nodes.py，HTTP 接口见 server.py。

执行流程：
  web_search → topic_suggest → prepare_chat → chat_call
                                                   │
                                            quality_check
                                                   │
                                              parse_qc
                                              ↑       │
                                 passed=False ┘        └ passed=True → exit
"""

from pathlib import Path

from ccserver import SessionManager
from ccserver.pipeline import Graph
from ccserver.mcp.manager import MCPManager

from nodes import (
    web_search, topic_suggest, prepare_chat,
    chat_call, quality_check, parse_qc,
)
from db import (
    create_session as db_create_session,
    session_exists as db_session_exists,
    save_turn as db_save_turn,
    get_history_list as db_get_history_list,
)

# ── 目录常量 ──────────────────────────────────────────────────────────────────

_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parent.parent.parent

PERSONAS_DIR = _HERE / "personas"
MCP_CONFIG = _HERE / ".mcp.json"

# ── 对话历史（SQLite 存储）────────────────────────────────────────────────────


def init_session(session_id: str, persona_id: str = "") -> None:
    db_create_session(session_id, persona_id)


def session_exists(session_id: str) -> bool:
    return db_session_exists(session_id)


def load_persona(persona_id: str) -> str:
    persona_file = PERSONAS_DIR / persona_id / "persona.md"
    if not persona_file.exists():
        raise FileNotFoundError(f"人设不存在: {persona_id}")
    return persona_file.read_text(encoding="utf-8")


def list_personas() -> list[str]:
    if not PERSONAS_DIR.exists():
        return []
    return [p.name for p in PERSONAS_DIR.iterdir() if p.is_dir()]


def get_history_list(session_id: str, k: int = 10) -> list[dict]:
    return db_get_history_list(session_id, k)


def format_history_text(history: list[dict]) -> str:
    if not history:
        return "（暂无历史）"
    lines = []
    for msg in history:
        role = "对方" if msg["role"] == "user" else "你"
        lines.append(f"{role}：{msg['content']}")
    return "\n".join(lines)


def save_turn(session_id: str, user_msg: str, assistant_msg: str) -> None:
    db_save_turn(session_id, user_msg, assistant_msg)


# ── Graph 定义 ────────────────────────────────────────────────────────────────


class PersonaChatGraph(Graph):
    """
    仿人类角色扮演对话 Graph（有环，含质量检测重试循环）。
    节点定义见 nodes.py。
    """

    max_steps = None

    def build(self):
        self.entry = "web_search"

        self.add_node(web_search)
        self.add_node(topic_suggest)
        self.add_node(prepare_chat)
        self.add_node(chat_call)
        self.add_node(quality_check)
        self.add_node(parse_qc)

        self.add_edge("web_search", "topic_suggest")
        self.add_edge("topic_suggest", "prepare_chat")
        self.add_edge("prepare_chat", "chat_call")
        self.add_edge("chat_call", "quality_check")
        self.add_edge("quality_check", "parse_qc")
        self.add_edge("parse_qc", "prepare_chat",
                      condition=lambda d: not d.get("passed", True))
        self.add_exit_edge("parse_qc",
                           condition=lambda d: d.get("passed", True))

    async def chat(self, user_input: str, persona_id: str, session_id: str) -> str:
        history_list = get_history_list(session_id)

        result = await self.run({
            "current_query": user_input,
            "persona": load_persona(persona_id),
            "history_str": format_history_text(history_list),
            "history_list": history_list,
            "topic_suggestion": "",
            "reflection": "",
            "web_search_result": "",
        })

        chat_response = result.get("chat_response", "")
        save_turn(session_id, user_input, chat_response)
        return chat_response


# ── MCP 初始化工厂 ─────────────────────────────────────────────────────────────


async def create_graph(session_manager: SessionManager) -> PersonaChatGraph:
    """server.py 在应用启动时 await 此函数。"""
    from ccserver.model import get_adapter

    mcp = MCPManager.from_config(MCP_CONFIG, project_dir=_HERE)
    await mcp.connect_all()
    return PersonaChatGraph(session_manager=session_manager, mcp=mcp, adapter=get_adapter())
