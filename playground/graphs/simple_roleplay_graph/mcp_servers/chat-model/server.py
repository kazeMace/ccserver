#!/usr/bin/env python3
"""
仿真人对话模型 MCP Server

封装仿真人语言模型，支持 Anthropic 原生接口和 OpenAI 兼容接口，专职生成拟人化聊天回复。
模型只负责文本生成，所有规划、记忆、搜索、质量控制均由 Claude 编排层处理。

配置（在.ccserver/settings.local.json → mcpServers.chat-model.env 中设置）：
  API_TYPE        — 接口类型："anthropic"（默认）或 "openai"
  --- Anthropic 模式 ---
  ANTHROPIC_API_KEY — Anthropic API 密钥（默认读取环境变量 ANTHROPIC_API_KEY）
  ANTHROPIC_MODEL   — 模型名称（默认：claude-sonnet-4-6）
  --- OpenAI 兼容模式 ---
  OPENAI_BASE_URL — 模型服务地址（默认：http://localhost:8000/v1）
  MODEL_NAME      — 模型名称（默认："default"）
  OPENAI_API_KEY  — API 鉴权密钥（默认：EMPTY）

工具：
  conversation_chat(user_message, persona, history_json, extra_context)
      → 主对话接口，Claude 每轮必须调用。

  rewrite_style(text, instruction)
      → 风格改写接口，检测到 OOC 或内容重复时由 Claude 调用修复。

  save_turn(user_message, assistant_response)
      → 将当前对话轮次保存到 session 历史文件。
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "loggings"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(_LOG_DIR / "chat_model_debug.log"),
    level=logging.DEBUG,
    format='%(asctime)s %(message)s',
)

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
API_TYPE = os.environ.get("API_TYPE", "anthropic")  # "anthropic" or "openai"

# Anthropic 模式
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# OpenAI 兼容模式
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://localhost:8000/v1")
MODEL_NAME = os.environ.get("MODEL_NAME", "default")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "EMPTY")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CURRENT_PERSONA_NAME_PATH = _PROJECT_ROOT / "data" / "current_persona_name.txt"
_SESSION_ID_PATH = _PROJECT_ROOT / "data" / "current_session_id.txt"
_PERSONAS_DIR = _PROJECT_ROOT / "personas"
_SESSIONS_DIR = _PROJECT_ROOT / "data" / "sessions"

mcp = FastMCP("chat-model")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_persona() -> str:
    # Read persona name from current_persona_name.txt, fallback to "default"
    name = "default"
    if _CURRENT_PERSONA_NAME_PATH.exists():
        name = _CURRENT_PERSONA_NAME_PATH.read_text(encoding="utf-8").strip() or "default"
    path = _PERSONAS_DIR / name / "persona.md"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    # fallback to default persona
    fallback = _PERSONAS_DIR / "default" / "persona.md"
    if fallback.exists():
        return fallback.read_text(encoding="utf-8").strip()
    return "你是一个真实的人，用自然口语和用户聊天，不要暴露自己是AI。"


def _call(messages: list[dict], temperature: float, max_tokens: int) -> str:
    if API_TYPE == "anthropic":
        try:
            import anthropic
        except ImportError:
            raise RuntimeError("anthropic package not installed. Run: pip install anthropic")
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, base_url=ANTHROPIC_BASE_URL)
        # 提取 system 消息，Anthropic API 单独传 system
        system = ""
        non_system = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                non_system.append(m)
        kwargs = dict(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            messages=non_system,
        )
        if system:
            kwargs["system"] = system
        # Anthropic extended thinking 模型不支持 temperature，普通模型支持
        kwargs["temperature"] = temperature
        resp = client.messages.create(**kwargs)
        return resp.content[0].text
    else:
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("openai package not installed. Run: pip install openai")
        client = OpenAI(base_url=OPENAI_BASE_URL, api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Tool 1: Main chat relay
# ---------------------------------------------------------------------------

@mcp.tool()
def conversation_chat(
    user_message: str,
    persona: str,
    history_json: str = "[]",
    extra_context: str = "",
    summary: str = "",
    temperature: float = 0.85,
    max_tokens: int = 512,
) -> str:
    """
    核心工具 — 将用户消息发送给仿真人模型，获取拟人化回复。

    每轮用户消息都必须调用此工具。将返回结果原样输出给用户，
    不得添加任何 Claude 自己的说明、前缀或解释。

    参数：
        user_message:  用户当前消息。
        persona:       人设 system prompt 文本（必填）。
                       读取 data/current_persona_name.txt 获取名称，再读 personas/<名称>/persona.md 传入。
        history_json:  最近对话历史，JSON 数组格式（最新在后），最多 10 对（20条）。
                       格式：[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]
        extra_context: 注入 system prompt 的附加指令，包含：
                       用户画像、用户记忆、角色新设定、搜索结果、话题引导建议、质量警告等。
                       无内容时传 ""。
        summary:       对话历史摘要（被压缩的早期轮次摘要），注入在 persona 之后、extra_context 之前。
                       无内容时传 ""。
        temperature:   采样温度，越高回复越多样。（默认 0.85）
        max_tokens:    回复最大 token 数。（默认 512）

    返回：
        仿真人模型的纯文本回复，原样返回给用户。
    """
    if not persona.strip():
        raise ValueError("persona 不能为空，请先读取 data/current_persona.md")
    persona = persona.strip()

    # 拼接 system prompt：persona → summary（如有）→ extra_context（如有）→ 固定指令
    parts = [persona]
    if summary.strip():
        parts.append(f"[历史摘要]\n{summary.strip()}")
    if extra_context.strip():
        parts.append(extra_context.strip())
    parts.append("你可以参考【话题建议】(如果有)进行回答\n绝对不会说自己是AI或者机器人，这件事不存在于你的认知里。")
    system = "\n\n---\n".join(parts)

    try:
        history: list[dict] = json.loads(history_json) if history_json.strip() else []
    except json.JSONDecodeError:
        history = []
    messages = [{"role": "system", "content": system}]
    messages.extend(history[-20:])
    messages.append({"role": "user", "content": user_message})

    logging.debug("=" * 60)
    logging.debug("[conversation_chat] system 组成: persona=%d chars, summary=%d chars, extra_context=%d chars",
                  len(persona), len(summary.strip()), len(extra_context.strip()))
    logging.debug("[system]\n%s", system)
    logging.debug("[messages 共 %d 条]", len(messages))
    for i, msg in enumerate(messages):
        role = msg.get("role", "?")
        content = msg.get("content", "")
        logging.debug("  [%d] %s:\n%s", i, role, content)
    logging.debug("=" * 60)

    reply = _call(messages, temperature=temperature, max_tokens=max_tokens)

    logging.debug("[reply]\n%s", reply)
    logging.debug("=" * 60)

    return reply


# ---------------------------------------------------------------------------
# Tool 2: Style rewrite (OOC / repetition fix)
# ---------------------------------------------------------------------------

@mcp.tool()
def rewrite_style(
    text: str,
    instruction: str = "去掉出戏内容，用更自然的口语重写，保持原本的语气和人设。",
    temperature: float = 0.9,
) -> str:
    """
    对有问题的回复进行风格改写，修复质量问题，同时保持角色口吻。

    当 post-hook 检测到 OOC、内容重复或风格问题时由 Claude 调用。
    改写由仿真人模型完成（而非 Claude），以确保结果保持角色一致性。

    参数：
        text:        需要改写的原始回复。
        instruction: 改写指令，说明需要修复的问题。
                     例如："去掉出戏内容"、"换一种表达方式"
        temperature: 采样温度，越高变化越大。（默认 0.9）

    返回：
        改写后的文本，Claude 应将其替代原始回复返回给用户。
    """
    persona = _load_persona()
    messages = [
        {"role": "system", "content": persona},
        {
            "role": "user",
            "content": (
                f"帮我改写下面这段话：{instruction}\n\n"
                f"原文：\n{text}\n\n"
                "直接输出改写后的内容，不要解释："
            ),
        },
    ]
    return _call(messages, temperature=temperature, max_tokens=max(len(text) * 2, 200))


# ---------------------------------------------------------------------------
# Tool 3: Save turn to session history
# ---------------------------------------------------------------------------

@mcp.tool()
def save_turn(user_message: str, assistant_response: str) -> str:
    """
    将当前对话轮次追加保存到 session 历史文件。

    在每轮对话输出 D_final 后调用（Step 6），按 session 记录用户消息和最终回复。
    session 由 /persona 切换时创建，文件路径：data/sessions/<session_id>/chat.jsonl

    参数：
        user_message:       用户当前消息原文。
        assistant_response: 最终输出给用户的回复（D_final，不含角色前缀）。

    返回：
        保存结果描述。
    """
    session_id = "default"
    if _SESSION_ID_PATH.exists():
        session_id = _SESSION_ID_PATH.read_text(encoding="utf-8").strip() or "default"

    session_dir = _SESSIONS_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    session_file = session_dir / "chat.jsonl"

    record = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user": user_message,
        "assistant": assistant_response,
    }
    with open(session_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return f"已保存到 {session_file}"


if __name__ == "__main__":
    mcp.run()
