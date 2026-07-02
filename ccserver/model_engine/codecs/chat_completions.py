"""
ccserver/model_engine/codecs/chat_completions.py

ChatCompletionsCodec — unified ↔ OpenAI Chat Completions API 双向编解码器。

支持所有 OpenAI-compatible API：OpenAI、OpenRouter、Ollama、LMStudio、OneAPI、
DeepSeek（含 reasoning_content 字段）等。

encode 方向：
  - encode_messages: UnifiedMessage 列表 → OpenAI messages 列表
    system 作为第一条 {"role": "system", "content": ...} 消息
    assistant 消息：text → content，UnifiedToolUseBlock → tool_calls
    user 消息：UnifiedToolResultBlock → {"role": "tool", ...} 独立消息
  - encode_tools: unified tool → OpenAI function tool 格式

decode 方向：
  - decode_response: ChatCompletion 响应 → UnifiedResponse
    含 reasoning_content/reasoning 字段（deepseek/openrouter）→ thinking
  - decode_stream_chunk: 流式 chunk → UnifiedStreamDelta | None
    tool_calls delta 累积到 StreamState，不产出 delta
  - finish_reason_hook: OpenAI finish_reason → 统一 stop_reason
  - _build_usage: prompt_tokens/completion_tokens → UnifiedUsage

迁移来源：
  ccserver/model_engine/adapters/openai.py 中的：
    _unified_blocks_to_openai_content → encode_messages 内部
    to_native_messages → encode_messages
    to_native_tools → encode_tools
    to_unified_message → decode_response
    _map_finish_reason → finish_reason_hook
    _openai_usage_to_unified → _build_usage

设计约束：
  - 不 import OpenAI SDK，只通过 getattr 读取 SDK 对象属性
  - 测试用 MagicMock 即可覆盖，无需真实 SDK

ChatCompletionsCodec — bidirectional codec for OpenAI-compatible Chat Completions APIs.
"""

from __future__ import annotations

import json

from loguru import logger

from ccserver.messages import (
    UnifiedTextBlock,
    UnifiedThinkingBlock,
    UnifiedToolUseBlock,
    UnifiedToolResultBlock,
    UnifiedImageBlock,
    UnifiedImageThumbnailBlock,
    UnifiedCommandBlock,
    UnifiedResponse,
    UnifiedStreamDelta,
    UnifiedToolCall,
    UnifiedUsage,
    StreamState,
)
from .base import ProtocolCodec


def _blocks_to_openai_content(blocks: list) -> "str | list":
    """
    UnifiedBlock 列表 → OpenAI content（纯文本时返回 str；含图像时返回 list）。
    过滤 UnifiedImageThumbnailBlock（内部块，不发给 API）。
    过滤 UnifiedCommandBlock（内部块，不发给 API）。

    这是一个纯函数辅助工具，供 encode_messages 内部调用。
    Pure helper: converts UnifiedBlock list to OpenAI content format.

    参数：
        blocks: list[UnifiedBlock]

    返回：
        str（纯文本）或 list[dict]（含图像的 multipart content）
    """
    # 检查是否含有图像块（只检查 UnifiedImageBlock，忽略 thumbnail）
    has_image = any(isinstance(b, UnifiedImageBlock) for b in blocks)

    if not has_image:
        # 纯文本：把所有 TextBlock 拼接为一个字符串
        text_parts = [
            b.text
            for b in blocks
            if isinstance(b, UnifiedTextBlock)
        ]
        return "\n".join(text_parts) if text_parts else ""

    # 含图像：构造 multipart content list
    openai_parts: list[dict] = []
    for block in blocks:
        if isinstance(block, UnifiedImageThumbnailBlock):
            # 内部块，跳过
            continue
        if isinstance(block, UnifiedCommandBlock):
            # 内部块，跳过
            continue
        if isinstance(block, UnifiedTextBlock):
            openai_parts.append({"type": "text", "text": block.text})
        elif isinstance(block, UnifiedImageBlock):
            source = block.source
            source_type = source.get("type", "")
            if source_type == "base64":
                media_type = source.get("media_type", "image/png")
                data = source.get("data", "")
                openai_parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{media_type};base64,{data}"},
                })
            elif source_type == "url":
                url = source.get("url", "")
                openai_parts.append({
                    "type": "image_url",
                    "image_url": {"url": url},
                })
            else:
                logger.warning(
                    "ChatCompletionsCodec: 不支持的 image source type: {}",
                    source_type,
                )
        else:
            logger.debug(
                "ChatCompletionsCodec: 忽略 multipart content 中的 block type: {}",
                type(block).__name__,
            )

    return openai_parts if openai_parts else ""


class ChatCompletionsCodec(ProtocolCodec):
    """
    OpenAI Chat Completions API 双向编解码器（兼容所有 OpenAI-compatible providers）。
    Bidirectional codec for OpenAI Chat Completions API and all compatible providers.
    """

    # ── Encode 方向（unified → native）──────────────────────────────────────

    def encode_messages(self, messages: list, system: "str | None" = None) -> dict:
        """
        unified messages → OpenAI messages 列表。

        system str 作为第一条 {"role": "system", "content": ...} 消息。
        assistant 消息中的 UnifiedToolUseBlock → tool_calls 格式。
        user 消息中的 UnifiedToolResultBlock → 独立的 {"role": "tool", ...} 消息。
        过滤 UnifiedImageThumbnailBlock 和 UnifiedCommandBlock（不发给 API）。

        参数：
            messages: list[UnifiedMessage] — 统一消息列表
            system: str | None — 系统提示

        返回：
            dict — {"messages": [...]}
        """
        openai_messages: list[dict] = []

        # system → 第一条消息
        if isinstance(system, list):
            # system 为 list[dict] 时提取文本
            system_texts = [
                b.get("text", "")
                for b in system
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if system_texts:
                openai_messages.append({"role": "system", "content": "\n".join(system_texts)})
        elif isinstance(system, str) and system:
            openai_messages.append({"role": "system", "content": system})

        for msg in messages:
            # 兼容裸 dict（直接追加）
            if isinstance(msg, dict):
                openai_messages.append(msg)
                continue

            role = msg.role

            if role == "assistant":
                self._encode_assistant_msg(msg.content, openai_messages)

            elif role == "user":
                self._encode_user_msg(msg.content, openai_messages)

            else:
                # 其他 role（如 system）：原样追加
                openai_messages.append({"role": role, "content": ""})

        return {"messages": openai_messages}

    def _encode_assistant_msg(self, blocks: list, out_messages: list) -> None:
        """
        assistant 消息内的 blocks → OpenAI assistant + tool_calls 格式，追加到 out_messages。

        逻辑：
          - UnifiedTextBlock → text_parts（拼接为 content）
          - UnifiedToolUseBlock → tool_calls 列表
          - UnifiedImageThumbnailBlock / UnifiedCommandBlock → 跳过
          - 其他 block → 记 debug 后跳过

        参数：
            blocks: list[UnifiedBlock]
            out_messages: list[dict] — 目标 openai messages 列表（原地追加）
        """
        text_parts: list[str] = []
        tool_calls: list[dict] = []

        for block in blocks:
            if isinstance(block, UnifiedImageThumbnailBlock):
                continue
            if isinstance(block, UnifiedCommandBlock):
                continue
            if isinstance(block, UnifiedTextBlock):
                text_parts.append(block.text)
            elif isinstance(block, UnifiedToolUseBlock):
                # 工具调用：input dict → JSON 字符串（OpenAI 规范）
                try:
                    arguments = json.dumps(block.input)
                except (TypeError, ValueError) as e:
                    logger.warning(
                        "ChatCompletionsCodec: tool_use block input JSON 序列化失败: {}",
                        e,
                    )
                    arguments = "{}"
                tool_calls.append({
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": arguments,
                    },
                })
            else:
                logger.debug(
                    "ChatCompletionsCodec._encode_assistant_msg: 跳过 block type={}",
                    type(block).__name__,
                )

        # 组装 assistant 消息
        assistant_msg: dict = {"role": "assistant"}
        assistant_msg["content"] = "".join(text_parts) if text_parts else None
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        out_messages.append(assistant_msg)

    def _encode_user_msg(self, blocks: list, out_messages: list) -> None:
        """
        user 消息内的 blocks → OpenAI user + tool 消息，追加到 out_messages。

        逻辑：
          - UnifiedToolResultBlock → 独立 {"role": "tool", "tool_call_id": ..., "content": ...}
          - 其他（含 image）→ 组装成 user 消息 content
          - UnifiedImageThumbnailBlock / UnifiedCommandBlock → 跳过

        参数：
            blocks: list[UnifiedBlock]
            out_messages: list[dict] — 目标 openai messages 列表（原地追加）
        """
        text_parts: list[str] = []
        non_tool_result_blocks: list = []

        for block in blocks:
            if isinstance(block, UnifiedImageThumbnailBlock):
                continue
            if isinstance(block, UnifiedCommandBlock):
                continue

            if isinstance(block, UnifiedToolResultBlock):
                # tool result → 独立 tool role 消息
                content = block.content
                if isinstance(content, list):
                    # 嵌套 blocks（如含图像的 tool result）
                    openai_content = _blocks_to_openai_content(content)
                elif isinstance(content, str):
                    openai_content = content
                else:
                    openai_content = str(content)
                out_messages.append({
                    "role": "tool",
                    "tool_call_id": block.tool_use_id,
                    "content": openai_content,
                })
            else:
                non_tool_result_blocks.append(block)

        # 非 tool_result 块组装为 user 消息
        if non_tool_result_blocks:
            content = _blocks_to_openai_content(non_tool_result_blocks)
            if content:
                out_messages.append({"role": "user", "content": content})

    def encode_tools(self, tools: "list | None") -> dict:
        """
        unified tool {name, description, input_schema} → OpenAI function tool 格式。
        空列表/None → {}。

        参数：
            tools: list | None — 工具定义列表（通用格式）

        返回：
            dict — {"tools": [...]} 或 {}
        """
        if not tools:
            return {}

        openai_tools: list[dict] = []
        for tool in tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            })
        return {"tools": openai_tools}

    # ── Decode 方向（native → unified）──────────────────────────────────────

    def decode_response(self, native_response) -> UnifiedResponse:
        """
        OpenAI ChatCompletion 响应 → UnifiedResponse（含 reasoning + usage）。

        reasoning_content / reasoning 字段（deepseek/openrouter 等）→ thinking。
        choices[0].message.content → content。
        choices[0].message.tool_calls → tool_calls。

        参数：
            native_response: OpenAI SDK ChatCompletion 对象

        返回：
            UnifiedResponse — 统一响应
        """
        assert native_response is not None, "decode_response: native_response 不能为 None"

        choice = native_response.choices[0]
        message = choice.message

        # 读取 reasoning 字段（deepseek / openrouter 等推理模型）
        reasoning = (
            getattr(message, "reasoning_content", None)
            or getattr(message, "reasoning", None)
        )
        thinking_text = str(reasoning) if reasoning else ""

        # 读取正文内容
        raw_content = message.content or ""
        content_text = self.text_decode_hook(raw_content) if raw_content else ""

        # 读取工具调用
        raw_tool_calls = message.tool_calls
        tool_calls = self.tool_decode_hook(raw_tool_calls) if raw_tool_calls else []

        # 停止原因映射
        stop_reason = self.finish_reason_hook(choice.finish_reason)

        # 用量
        usage = self._build_usage(getattr(native_response, "usage", None))

        return UnifiedResponse(
            content=content_text,
            thinking=thinking_text,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
        )

    def decode_stream_chunk(
        self, chunk, stream_state: StreamState
    ) -> "UnifiedStreamDelta | None":
        """
        OpenAI 流式 chunk → UnifiedStreamDelta | None。

        处理优先级：
          1. reasoning_content / reasoning → thinking delta（累积 state.thinking_chunks）
          2. content → text delta（累积 state.text_chunks）
          3. tool_calls → 累积 state.tool_calls_raw（不产出 delta）

        注：一个 chunk 只产出第一个非 None 的 delta（reasoning 或 text）。
        tool_calls 永远不产出 delta（仅累积）。

        参数：
            chunk: OpenAI SDK stream chunk 对象
            stream_state: 可变累积状态

        返回：
            UnifiedStreamDelta | None
        """
        delta = chunk.choices[0].delta

        # 1. reasoning 字段（deepseek / openrouter 等推理模型）
        reasoning = (
            getattr(delta, "reasoning_content", None)
            or getattr(delta, "reasoning", None)
        )
        if reasoning:
            stream_state.thinking_chunks.append(reasoning)
            return UnifiedStreamDelta(kind="thinking", text=reasoning)

        # 2. 正文文本内容
        if delta.content:
            stream_state.text_chunks.append(delta.content)
            return UnifiedStreamDelta(kind="text", text=delta.content)

        # 3. 工具调用（按 index 累积，不产出 delta）
        if delta.tool_calls:
            self._accumulate_tool_calls(delta.tool_calls, stream_state)

        return None

    def _accumulate_tool_calls(self, tool_calls_delta: list, stream_state: StreamState) -> None:
        """
        累积流式 tool_calls delta 到 stream_state.tool_calls_raw。

        OpenAI 的 tool_calls 按 index 分片到达，arguments 需要逐步拼接。

        参数：
            tool_calls_delta: tool_calls delta 列表
            stream_state: 可变累积状态
        """
        for tc in tool_calls_delta:
            idx = tc.index
            # 初始化该 index 的 entry
            if idx not in stream_state.tool_calls_raw:
                stream_state.tool_calls_raw[idx] = {"id": "", "name": "", "arguments": ""}

            entry = stream_state.tool_calls_raw[idx]

            # 累积 id（通常在第一个 fragment 中）
            if tc.id:
                entry["id"] = tc.id

            # 累积 function.name（通常在第一个 fragment 中）
            func = getattr(tc, "function", None)
            if func:
                if getattr(func, "name", None):
                    entry["name"] = func.name
                # 累积 arguments（分多个 fragment 拼接）
                if getattr(func, "arguments", None):
                    entry["arguments"] += func.arguments

    def finish_reason_hook(self, raw_reason: "str | None") -> str:
        """
        OpenAI finish_reason → 统一 stop_reason。

        映射表：
          "stop"       → "end_turn"
          "tool_calls" → "tool_use"
          "length"     → "max_tokens"
          其他/None    → "end_turn"

        参数：
            raw_reason: OpenAI finish_reason 字符串（或 None）

        返回：
            str — 统一 stop_reason
        """
        mapping = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }
        return mapping.get(raw_reason or "", "end_turn")

    def tool_decode_hook(self, raw_tool_calls) -> list:
        """
        OpenAI SDK tool_calls 列表 → list[UnifiedToolCall]。

        arguments 字段为 JSON 字符串，解析为 dict；解析失败时降级为 {"raw": arguments}。

        参数：
            raw_tool_calls: OpenAI SDK tool_calls 对象列表（或 None）

        返回：
            list[UnifiedToolCall]
        """
        if not raw_tool_calls:
            return []

        result: list[UnifiedToolCall] = []
        for tc in raw_tool_calls:
            func = getattr(tc, "function", None)
            if func is None:
                continue

            arguments = getattr(func, "arguments", None) or "{}"
            try:
                input_dict = json.loads(arguments)
            except (json.JSONDecodeError, ValueError):
                logger.warning(
                    "ChatCompletionsCodec.tool_decode_hook: JSON 解析失败: {}",
                    arguments,
                )
                input_dict = {"raw": arguments}

            result.append(UnifiedToolCall(
                id=getattr(tc, "id", ""),
                name=getattr(func, "name", ""),
                input=input_dict,
            ))

        return result

    def _build_usage(self, usage_raw) -> "UnifiedUsage | None":
        """
        OpenAI SDK usage → UnifiedUsage。
        None → None。

        字段映射：
          prompt_tokens     → input_tokens
          completion_tokens → output_tokens
          total_tokens      → total_tokens

        参数：
            usage_raw: OpenAI SDK usage 对象（或 None）

        返回：
            UnifiedUsage | None
        """
        if usage_raw is None:
            return None

        return UnifiedUsage(
            input_tokens=getattr(usage_raw, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage_raw, "completion_tokens", 0) or 0,
            total_tokens=getattr(usage_raw, "total_tokens", 0) or 0,
        )
