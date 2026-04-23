"""
translator — Anthropic <-> OpenAI 消息/Schema 转换器。

职责：把 Anthropic 格式的输入转成 OpenAI 格式请求参数，
以及把 OpenAI 响应内容还原成 Anthropic 对象。

所有内部业务代码继续保留 Anthropic block 格式，
只有 OpenAIAdapter 会调用这里的转换函数。
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger


# ─── Anthropic -> OpenAI 消息转换 ─────────────────────────────────────────────


def anthropic_to_openai_messages(
    messages: list[dict],
    system: list[dict] | str | None = None,
) -> list[dict]:
    """
    将 Anthropic 格式的 messages + system 转换为 OpenAI 的 messages 列表。

    规则：
      1. system 为 list[dict]（text blocks）时，提取所有文本拼接为单个字符串，
         转换为 messages 列表开头的 {"role": "system", "content": text}。
      2. user/assistant 消息中：
         - text block -> 直接取 text 字符串
         - tool_use block（assistant）-> 转换为 assistant 消息的 tool_calls 数组
         - tool_result block（user）-> 转换为独立的 {"role": "tool", ...} 消息
    """
    openai_messages: list[dict] = []

    # 处理 system prompt
    if isinstance(system, list):
        system_texts = []
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                system_texts.append(block.get("text", ""))
        if system_texts:
            openai_messages.append({
                "role": "system",
                "content": "\n".join(system_texts),
            })
    elif isinstance(system, str) and system:
        openai_messages.append({"role": "system", "content": system})

    # 处理 messages
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "assistant":
            # assistant 消息可能包含 text 和 tool_use blocks
            text_parts: list[str] = []
            tool_calls: list[dict] = []

            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    if block_type == "text":
                        text_parts.append(block.get("text", ""))
                    elif block_type == "tool_use":
                        tool_calls.append({
                            "id": block["id"],
                            "type": "function",
                            "function": {
                                "name": block["name"],
                                "arguments": json.dumps(block.get("input", {})),
                            },
                        })
            elif isinstance(content, str):
                text_parts.append(content)

            assistant_msg: dict = {"role": "assistant"}
            if text_parts:
                assistant_msg["content"] = "".join(text_parts)
            else:
                assistant_msg["content"] = None
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            openai_messages.append(assistant_msg)

        elif role == "user":
            # user 消息可能包含 text 和 tool_result blocks
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    if block_type == "text":
                        text_parts.append(block.get("text", ""))
                    elif block_type == "tool_result":
                        tool_content = block.get("content", "")
                        # OpenAI role="tool" 只接受字符串，非字符串做降级处理
                        if not isinstance(tool_content, str):
                            tool_content = str(tool_content)
                        openai_messages.append({
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": tool_content,
                        })
                # 如果有纯文本部分，追加一条 user 消息
                if text_parts:
                    openai_messages.append({
                        "role": "user",
                        "content": "".join(text_parts),
                    })
            elif isinstance(content, str):
                openai_messages.append({"role": "user", "content": content})
            else:
                openai_messages.append({"role": "user", "content": str(content)})
        else:
            # 其他 role 原样传递
            openai_messages.append(dict(msg))

    return openai_messages


# ─── Anthropic -> OpenAI Schema 转换 ──────────────────────────────────────────


def anthropic_to_openai_tools(tools: list[dict] | None) -> list[dict] | None:
    """
    将 Anthropic 格式的 tools 列表转换为 OpenAI 格式。

    Anthropic: {"name": "...", "description": "...", "input_schema": {...}}
    OpenAI:    {"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}
    """
    if tools is None:
        return None

    openai_tools: list[dict] = []
    for tool in tools:
        name = tool.get("name", "")
        description = tool.get("description", "")
        input_schema = tool.get("input_schema", {})
        openai_tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": input_schema,
            },
        })
    return openai_tools


# ─── OpenAI -> Anthropic 响应转换 ─────────────────────────────────────────────


def openai_to_anthropic_message(openai_response) -> "_Message":
    """
    将 OpenAI ChatCompletion 响应转换为模拟 Anthropic Message 的对象。

    返回 _Message 对象，具有 .content 列表和 .stop_reason 字符串。
    """
    choice = openai_response.choices[0]
    message = choice.message
    content: list[dict] = []

    # text content
    if message.content:
        content.append({"type": "text", "text": message.content})

    # tool_calls -> tool_use blocks
    if message.tool_calls:
        for tc in message.tool_calls:
            arguments = tc.function.arguments or "{}"
            try:
                input_dict = json.loads(arguments)
            except json.JSONDecodeError:
                logger.warning("Failed to parse tool_call arguments as JSON: {}", arguments)
                input_dict = {"raw": arguments}
            content.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.function.name,
                "input": input_dict,
            })

    finish_reason = choice.finish_reason
    stop_reason = _map_finish_reason(finish_reason)

    return _Message(content=content, stop_reason=stop_reason)


def _map_finish_reason(finish_reason: str | None) -> str | None:
    """将 OpenAI finish_reason 映射为 Anthropic stop_reason。"""
    if finish_reason == "stop":
        return "end_turn"
    if finish_reason == "tool_calls":
        return "tool_use"
    if finish_reason == "length":
        return "max_tokens"
    return None


# ─── 模拟 Anthropic 数据类 ────────────────────────────────────────────────────


class _Message:
    """模拟 Anthropic SDK 的 Message 对象。"""

    def __init__(self, content: list[dict], stop_reason: str | None):
        self.content = content
        self.stop_reason = stop_reason


class _TextBlock:
    """模拟 Anthropic SDK 的 TextBlock 对象。"""

    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class _ToolUseBlock:
    """模拟 Anthropic SDK 的 ToolUseBlock 对象。"""

    def __init__(self, id: str, name: str, input: dict):
        self.type = "tool_use"
        self.id = id
        self.name = name
        self.input = input
