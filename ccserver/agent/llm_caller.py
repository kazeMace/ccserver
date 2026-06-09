"""
agent.llm_caller — LLM 调用与消息序列净化。

背景：
  原 Agent 有 _call_llm_stream / _call_llm_sync 两个方法,二者约 90% 重复
  (相同的 hook 触发、消息构建、重试、事件发布),仅"实际调用 LLM"一处不同:
    - stream=True : adapter.stream(...) 逐块 emit token,再取 final_message
    - stream=False: adapter.create(...) 一次性返回

设计：
  合并为 LLMCaller.call(stream: bool),消除重复(DRY)。共享脚手架只写一遍,
  仅在真正调用 LLM 处按 stream 分支。行为与重构前逐字一致。
  _sanitize_messages 是纯函数(消息序列修复),一并迁出为 sanitize_messages 静态方法。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from anthropic import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError
from loguru import logger

from ..event_bus import AgentEvent, EventType, SenderType
from ..utils import get_block_attr
from .runtime import AgentRuntime

# 重试配置:与原实现一致(最多 3 次,退避 2/5/10 秒)
_MAX_RETRIES = 3
_RETRY_DELAYS = [2, 5, 10]


class LLMCaller:
    """
    LLM 调用器,被 Agent 持有(组合)。

    依赖 AgentRuntime 提供 adapter/model/system/_schemas/session/emitter 等。
    """

    def __init__(self, rt: AgentRuntime):
        self._rt = rt

    async def call(self, *, stream: bool):
        """
        调用 LLM 并返回 response;失败(重试耗尽)返回 None。

        Args:
            stream: True 走流式(实时 emit token),False 走非流式(一次性返回)。

        合并自原 _call_llm_stream / _call_llm_sync,逻辑逐字一致,
        仅"实际调用 LLM"一处按 stream 分支。
        """
        rt = self._rt
        await rt._set_phase("llm_calling")

        for attempt in range(_MAX_RETRIES):
            try:
                # hook: prompt:build:before — 可修改 system/messages
                build_hook = await rt.session.hooks.emit(
                    "prompt:build:before",
                    {
                        "system": rt.system,
                        "messages": [dict(m) for m in rt.context.messages],
                        "model": rt.model,
                    },
                    rt._build_hook_ctx(),
                )
                # hook 可修改 system（替换或追加）
                effective_system = build_hook.system_message or rt.system
                # hook 可追加 additional_context 到最后一条 user 消息
                if build_hook.additional_context:
                    msgs = [dict(m) for m in rt.context.messages]
                    if msgs and msgs[-1].get("role") == "user":
                        msgs[-1]["content"] = msgs[-1].get("content", "") + "\n\n" + build_hook.additional_context
                    effective_messages = msgs
                else:
                    effective_messages = [dict(m) for m in rt.context.messages]

                # hook: prompt:llm:input — 观测即将发送给 LLM 的完整输入
                await rt.session.hooks.emit_void(
                    "prompt:llm:input",
                    {"messages": effective_messages, "model": rt.model},
                    rt._build_hook_ctx(),
                )
                # 兜底验证：hook 可能修改了消息序列，确保 tool_use -> tool_result 配对完整
                self.sanitize_messages(effective_messages)

                # 发布 llm_request 事件，供 monitor 追踪 LLM 调用
                llm_start_ts = datetime.now(timezone.utc)
                await rt.session.event_bus.publish(AgentEvent(
                    type=EventType.LLM_REQUEST,
                    agent_id=rt.context.agent_id,
                    session_id=rt.session.id,
                    sender_type=SenderType.AGENT,
                    payload={
                        "model": rt.model,
                        "message_count": len(effective_messages),
                        "tools_count": len(rt._schemas),
                        "system_len": len(effective_system) if effective_system else 0,
                        "attempt": attempt + 1,
                    },
                ))

                # ── 实际调用 LLM(唯一按 stream 分支处)──────────────────────────
                if stream:
                    async with rt.adapter.stream(
                        model=rt.model,
                        system=effective_system,
                        messages=effective_messages,
                        tools=rt._schemas,
                        max_tokens=8000,
                    ) as stream_ctx:
                        # 遍历事件流,区分 text_delta(正文)和 thinking_delta(思考过程)
                        async for chunk in stream_ctx:
                            chunk_type = getattr(chunk, "type", None)
                            if chunk_type == "content_block_delta":
                                delta = getattr(chunk, "delta", None)
                                delta_type = getattr(delta, "type", None)
                                if delta_type == "text_delta":
                                    await rt.emitter.emit_token(getattr(delta, "text", ""))
                                elif delta_type == "thinking_delta":
                                    await rt.emitter.emit_thinking(getattr(delta, "thinking", ""))
                        response = await stream_ctx.get_final_message()
                else:
                    response = await rt.adapter.create(
                        model=rt.model,
                        system=effective_system,
                        messages=effective_messages,
                        tools=rt._schemas,
                        max_tokens=8000,
                    )

                # 发布 llm_response 事件
                llm_duration_ms = int((datetime.now(timezone.utc) - llm_start_ts).total_seconds() * 1000)
                content_blocks = response.content if hasattr(response, "content") else []
                await rt.session.event_bus.publish(AgentEvent(
                    type=EventType.LLM_RESPONSE,
                    agent_id=rt.context.agent_id,
                    session_id=rt.session.id,
                    sender_type=SenderType.AGENT,
                    payload={
                        "model": rt.model,
                        "stop_reason": response.stop_reason,
                        "content_blocks_count": len(content_blocks),
                        "duration_ms": llm_duration_ms,
                    },
                ))
                return response

            except (APIConnectionError, APITimeoutError, httpx.RemoteProtocolError, InternalServerError, RateLimitError) as e:
                if attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_DELAYS[attempt]
                    logger.warning(
                        "LLM network error, retrying ({}/{}) | agent={} delay={}s error={}",
                        attempt + 1, _MAX_RETRIES, rt.aid_label, delay, e,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error("LLM error after {} retries | agent={} error={}", _MAX_RETRIES, rt.aid_label, e)
                    await rt.emitter.emit_error(str(e))
                    await rt.session.hooks.emit_void(
                        "prompt:llm:error",
                        {"error": str(e), "model": rt.model},
                        rt._build_hook_ctx(),
                    )
                    return None

            except Exception as e:
                logger.error(
                    "LLM error | agent={} exc_type={} error={}",
                    rt.aid_label, type(e).__name__, e,
                )
                await rt.emitter.emit_error(str(e))
                await rt.session.hooks.emit_void(
                    "prompt:llm:error",
                    {"error": str(e), "model": rt.model},
                    rt._build_hook_ctx(),
                )
                return None

        return None  # 不会到达

    # ── 消息序列净化(纯函数,迁自 Agent._sanitize_messages)────────────────────────

    @staticmethod
    def sanitize_messages(messages: list[dict]) -> bool:
        """
        验证并修复消息序列，确保符合 Anthropic API 的消息顺序要求。

        API 规则：assistant 消息中包含 tool_use 块时，下一条消息必须是 user 角色，
        且包含对应的 tool_result 块（tool_use_id 匹配）。

        如果外部消息（如用户通过 channel 发送的新输入）被并发插入到 tool_use 和
        tool_result 之间，会导致 API 报 "tool call result does not follow tool call" 错误。

        修复方式：在不完整的 tool_use 后插入空的 tool_result，将外部消息后移。

        Args:
            messages: 消息列表（会被原地修改）

        Returns:
            是否做了修复
        """
        fixed = False
        i = 0
        while i < len(messages):
            msg = messages[i]
            if msg.get("role") != "assistant":
                i += 1
                continue

            # 检查 assistant 消息是否包含 tool_use 块
            content = msg.get("content", [])
            if not isinstance(content, list):
                i += 1
                continue

            tool_use_ids = [
                get_block_attr(b, "id")
                for b in content
                if isinstance(b, dict) and get_block_attr(b, "type") == "tool_use"
                if get_block_attr(b, "id")
            ]
            if not tool_use_ids:
                i += 1
                continue

            # 检查下一条消息
            if i + 1 >= len(messages):
                # tool_use 是列表最后一条，上一轮可能中断
                tool_results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": "[Tool call was interrupted. No result available.]",
                    }
                    for tid in tool_use_ids
                ]
                messages.append({
                    "role": "user",
                    "content": tool_results,
                })
                logger.warning(
                    "Fixed dangling tool_use at end | tool_use_ids={}",
                    tool_use_ids,
                )
                fixed = True
                break

            next_msg = messages[i + 1]
            if next_msg.get("role") != "user":
                # 下一条不是 user，序列被破坏（可能是外部 system/user 消息插入）
                tool_results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": "[Tool call was interrupted by new input. No result available.]",
                    }
                    for tid in tool_use_ids
                ]
                messages.insert(i + 1, {
                    "role": "user",
                    "content": tool_results,
                })
                logger.warning(
                    "Fixed broken tool_use sequence | tool_use_ids={} next_role={}",
                    tool_use_ids, next_msg.get("role"),
                )
                fixed = True
                i += 2  # 跳过插入的 tool_result，继续检查后续
                continue

            # 下一条是 user，检查 content 是否包含对应的 tool_result
            next_content = next_msg.get("content", [])
            if isinstance(next_content, str):
                # user 消息的 content 是字符串（普通文本），不是 tool_result
                tool_results = [
                    {
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": "[Tool call was interrupted by new input. No result available.]",
                    }
                    for tid in tool_use_ids
                ]
                messages.insert(i + 1, {
                    "role": "user",
                    "content": tool_results,
                })
                logger.warning(
                    "Fixed broken tool_use sequence | tool_use_ids={} next_content=string",
                    tool_use_ids,
                )
                fixed = True
                i += 2
                continue

            # next_content 是 list，检查是否包含所有对应的 tool_result
            result_ids = set()
            for block in next_content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tid = block.get("tool_use_id")
                    if tid:
                        result_ids.add(tid)

            missing_ids = set(tool_use_ids) - result_ids
            if missing_ids:
                # 部分 tool_use 没有对应的 tool_result（比较少见）
                for tid in missing_ids:
                    next_content.append({
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": "[Tool call was interrupted by new input. No result available.]",
                    })
                logger.warning(
                    "Fixed partial tool_use sequence | missing_ids={}",
                    list(missing_ids),
                )
                fixed = True

            i += 1

        return fixed
