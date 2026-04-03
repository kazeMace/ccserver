"""
nodes — 所有节点定义（包含 FunctionNode 的处理函数）。

每个节点描述"是什么"，不包含图结构（边、入口、执行顺序）。
"""

import json
import os
from pathlib import Path
from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from ccserver.pipeline import AgentNode, FunctionNode, MCPToolNode, NodeData
from ccserver.model import AnthropicAdapter

# ── 目录常量 ──────────────────────────────────────────────────────────────────

_HERE = Path(__file__).parent

load_dotenv(_HERE / ".env")
_PROJECT_ROOT = _HERE.parent.parent.parent

QUALITY_CHECK_DIR = _PROJECT_ROOT / "playground" / "agents" / "quality_check"
WEB_SEARCH_DIR    = _PROJECT_ROOT / "playground" / "agents" / "web_search"
TOPIC_SUGGEST_DIR = _PROJECT_ROOT / "playground" / "agents" / "topic_suggest"

# ── FunctionNode 处理函数 ─────────────────────────────────────────────────────


def prepare_chat_func(node_input: NodeData) -> dict:
    """
    为 chat-model MCP 工具准备参数。

    输入：history_list、topic_suggestion、reflection
    输出：history_json、extra_context
    """
    history_list = node_input.get("history_list", [])
    topic_suggestion = node_input.get("topic_suggestion", "")
    reflection = node_input.get("reflection", "")

    history_json = json.dumps(history_list, ensure_ascii=False)

    extra_parts = []
    if topic_suggestion:
        extra_parts.append(f"【话题建议】\n{topic_suggestion}")
    if reflection:
        extra_parts.append(f"【质量修复建议】\n{reflection}")
    extra_context = "\n\n".join(extra_parts)

    return {
        "history_json": history_json,
        "extra_context": extra_context,
    }


def parse_qc_func(node_input: NodeData) -> dict:
    """
    解析 quality-check 节点返回的 JSON 字符串。
    输出：passed、reflection
    """
    qc_raw = node_input.get("qc_raw", "")

    try:
        start = qc_raw.find("{")
        end = qc_raw.rfind("}") + 1
        qc_data = json.loads(qc_raw[start:end]) if start >= 0 else {}
    except (json.JSONDecodeError, ValueError):
        qc_data = {}

    return {
        "passed": bool(qc_data.get("passed", True)),
        "reflection": qc_data.get("reflection", ""),
    }



# ── 共享 ModelAdapter（从 .env 读取 key 和 base_url）────────────────────────────

_adapter = AnthropicAdapter(AsyncAnthropic(
    api_key=os.environ.get("ANTHROPIC_API_KEY"),
    base_url=os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
))

# ── 节点定义 ──────────────────────────────────────────────────────────────────

web_search = AgentNode(
    id="web_search",
    agent_dir=WEB_SEARCH_DIR,
    prompt=(
        "# 用户发言\n{current_query}\n\n"
        "# 历史对话\n{history_str}\n\n"
        "判断是否需要联网搜索，不需要则返回（无需搜索）"
    ),
    adapter=_adapter,
    model="claude-haiku-4-5-20251001",
    output_key="web_search_result",
    keep_session=False,
    agent_config={"prompt_version": "simple_agent:v0.0.1", "language": "简体中文"}
)

topic_suggest = AgentNode(
    id="topic_suggest",
    agent_dir=TOPIC_SUGGEST_DIR,
    system_file="topic-suggest.md",
    adapter=_adapter,
    model="claude-haiku-4-5-20251001",
    prompt=(
        "# PERSONA\n{persona}\n\n"
        "# 搜索结果\n{web_search_result}\n\n"
        "# 记忆搜索\n无\n\n"
        "# 最近对话\n{history_str}\n\n"
        "请给出本轮话题引导建议（1-2句）。"
    ),
    output_key="topic_suggestion",
    keep_session=False,
    agent_config={"prompt_version": "simple_agent:v0.0.1", "language": "简体中文"}

)

prepare_chat = FunctionNode(
    id="prepare_chat",
    func=prepare_chat_func,
)

chat_call = MCPToolNode(
    id="chat_call",
    server="chat-model",
    tool="conversation_chat",
    args_map={
        "user_message": "{current_query}",
        "persona": "{persona}",
        "history_json": "{history_json}",
        "extra_context": "{extra_context}",
    },
    output_key="chat_response",
)

quality_check = AgentNode(
    id="quality_check",
    system=(QUALITY_CHECK_DIR / "quality-check.md").read_text(encoding="utf-8"),
    adapter=_adapter,
    model="claude-haiku-4-5-20251001",
    prompt=(
        "response={chat_response}\n"
        "history={history_str}\n"
        "persona={persona}"
    ),
    output_key="qc_raw",
    keep_session=False,
    agent_config={"prompt_version": "simple_agent:v0.0.1", "language": "简体中文"}
)

parse_qc = FunctionNode(
    id="parse_qc",
    func=parse_qc_func,
)
