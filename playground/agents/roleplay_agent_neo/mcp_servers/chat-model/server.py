#!/usr/bin/env python3
"""
仿真人对话模型 MCP Server

封装仿真人语言模型，支持 Anthropic 原生接口和 OpenAI 兼容接口，专职生成拟人化聊天回复。
模型只负责文本生成，所有规划、记忆、搜索、质量控制均由 Claude 编排层处理。

配置（在 .claude/settings.local.json → mcpServers.chat-model.env 中设置）：
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
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# OpenAI 兼容模式
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://localhost:8000/v1")
MODEL_NAME = os.environ.get("MODEL_NAME", "default")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "EMPTY")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CURRENT_PERSONA_NAME_PATH = _PROJECT_ROOT / "data" / "current_persona_name.txt"
_PERSONAS_DIR = _PROJECT_ROOT / "personas"

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
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY or None)
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
        # resp.content 可能是多个 block（ThinkingBlock + TextBlock），取第一个 TextBlock 的文本
        for block in resp.content:
            if hasattr(block, "text"):
                return block.text
        # 没有 TextBlock，返回空字符串
        return ""
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
    conversation_id: str,
    extra_context: str,
    temperature: float = 0.85,
    max_tokens: int = 512,
) -> str:
    """
    核心工具 — 将用户消息发送给仿真人模型，获取拟人化回复。

    每轮用户消息都必须调用此工具。将返回结果原样输出给用户，
    不得添加任何 Claude 自己的说明、前缀或解释。

    调用前置条件（编排核心必须先执行）：
        save_message(conversation_id, "user", <用户消息>) — 将用户消息存入 DB，本工具从 DB 自动读取。

    以下内容全部从 chat.db 自动读取：
        - user_message : messages 表最新一条 role=user 的记录
        - persona      : sessions.persona_name → personas.persona_content
        - summary      : summaries 表
        - history      : messages 表最近 10 轮（20 条）

    参数：
        conversation_id: 会话 ID，由 hook 注入到上下文中的 [CONVERSATION_ID] 字段。
        extra_context:   注入 system prompt 的附加指令，包含：
                         用户画像、用户记忆、角色新设定、搜索结果、话题引导建议、质量警告等。
                         无内容时传 ""。

    返回：
        仿真人模型的纯文本回复，原样返回给用户。
    """
    import sqlite3 as _sqlite3

    conv_id = conversation_id.strip()
    if not conv_id:
        raise ValueError("conversation_id 不能为空")

    db_path = _PROJECT_ROOT / "chat.db"
    if not db_path.exists():
        raise RuntimeError(f"chat.db 不存在，请先运行 python scripts/init_db.py")

    try:
        conn = _sqlite3.connect(str(db_path))
        conn.row_factory = _sqlite3.Row

        # user_message：最新一条 role=user
        row = conn.execute(
            "SELECT content FROM messages WHERE conversation_id = ? AND role = 'user' ORDER BY id DESC LIMIT 1",
            (conv_id,),
        ).fetchone()
        if row is None:
            conn.close()
            raise ValueError("DB 中未找到用户消息，请先调用 save_message('user', ...)")
        user_message = row["content"]

        # persona：sessions → personas
        session_row = conn.execute(
            "SELECT persona_name FROM sessions WHERE conversation_id = ?", (conv_id,)
        ).fetchone()
        if session_row is None:
            conn.close()
            raise ValueError(f"未找到 session：{conv_id}，请先调用 create_session()")
        persona_name = session_row["persona_name"]

        persona_row = conn.execute(
            "SELECT persona_content FROM personas WHERE name = ?", (persona_name,)
        ).fetchone()
        persona = persona_row["persona_content"].strip() if persona_row else ""
        if not persona:
            conn.close()
            raise ValueError(f"personas 表中未找到角色：{persona_name}")

        # summary
        summary_row = conn.execute(
            "SELECT content FROM summaries WHERE conversation_id = ?", (conv_id,)
        ).fetchone()
        summary = summary_row["content"].strip() if summary_row else ""

        # history：最近 10 轮，排除最后一条 user（即当前消息，避免重复）
        history_rows = conn.execute(
            """
            SELECT role, content FROM (
                SELECT id, role, content FROM messages
                WHERE conversation_id = ?
                ORDER BY id DESC LIMIT 21
            ) ORDER BY id ASC
            """,
            (conv_id,),
        ).fetchall()
        conn.close()

        # 去掉最后一条（当前 user_message），剩余作为 history
        all_msgs = [{"role": r["role"], "content": r["content"]} for r in history_rows]
        history = all_msgs[:-1] if all_msgs and all_msgs[-1]["role"] == "user" else all_msgs

    except (ValueError, RuntimeError):
        raise
    except Exception as e:
        raise RuntimeError(f"读取 DB 失败: {e}")

    # 拼接 system prompt：persona → summary（如有）→ extra_context（如有）→ 固定指令
    parts = [persona]
    if summary:
        parts.append(f"[历史摘要]\n{summary}")
    if extra_context.strip():
        parts.append(extra_context.strip())
    parts.append("你可以参考【话题建议】(如果有)进行回答\n绝对不会说自己是AI或者机器人，这件事不存在于你的认知里。")
    system = "\n\n---\n".join(parts)

    messages = [{"role": "system", "content": system}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    logging.debug("=" * 60)
    logging.debug("[system 组成]")
    logging.debug("  persona 长度: %d chars", len(persona))
    logging.debug("  summary 长度: %d chars", len(summary.strip()))
    logging.debug("  extra_context 长度: %d chars", len(extra_context.strip()))
    logging.debug("[system 完整内容]\n%s", system)
    logging.debug("[messages 共 %d 条]", len(messages))
    for i, msg in enumerate(messages):
        role = msg.get("role", "?")
        content = msg.get("content", "")
        preview = content[:200] + ("..." if len(content) > 200 else "")
        logging.debug("  [%d] %s: %s", i, role, preview)
    logging.debug("=" * 60)

    return _call(messages, temperature=temperature, max_tokens=max_tokens)


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
def save_turn(conversation_id: str, assistant_response: str) -> str:
    """
    将 assistant 回复保存到 chat.db。

    编排流程：
      调用前：mcp__db__save_message(conversation_id, "user", <用户消息>)  ← 编排核心先存用户消息
      调用时：conversation_chat(conversation_id, extra_context)           ← 自动从 DB 读取 user_message
      调用后：save_turn(conversation_id, <assistant 回复>)                ← 本工具只存 assistant 一条

    参数：
        conversation_id:    会话 ID，由 hook 注入到上下文中的 [CONVERSATION_ID] 字段。
        assistant_response: 最终输出给用户的回复（D_final）。

    返回：
        保存结果描述。
    """
    import sqlite3 as _sqlite3

    conv_id = conversation_id.strip()
    if not conv_id:
        return "[WARN] conversation_id 为空，消息未保存"

    db_path = _PROJECT_ROOT / "chat.db"
    if not db_path.exists():
        return f"[WARN] chat.db 不存在，请先运行 python scripts/init_db.py"

    now = datetime.now().isoformat(timespec="seconds")
    try:
        conn = _sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (conv_id, "assistant", assistant_response, now),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        return f"[ERROR] 保存失败：{e}"

    return f"assistant 回复已保存，conversation_id={conversation_id}"


if __name__ == "__main__":
    mcp.run()
