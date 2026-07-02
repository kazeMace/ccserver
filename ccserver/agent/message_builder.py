"""
agent.message_builder — L2 造消息器。

职责（SRP）：造出"可发送给 LLM"的 system/messages：
  ① prompt:build:before hook（可改 system / 追加 additional_context）
  ② sanitize_messages（修复 tool_use → tool_result 配对）
  ③ prompt:llm:input hook（观测最终输入）

不负责调用 LLM、不发遥测事件、不推 token——那些由 L1 LLMCaller 与 Agent 自身承担。
依赖 AgentRuntime（rt）读取 system / context.messages / session.hooks。
"""

from __future__ import annotations

from loguru import logger

from ..utils import get_block_attr


class MessageBuilder:
    """L2 造消息器。详见模块 docstring。"""

    def __init__(self, rt):
        """
        Args:
            rt: AgentRuntime，提供 system / context / session.hooks。
        """
        self._rt = rt

    async def build(self):
        """
        构建并净化要发送的 (effective_system, effective_messages)。

        步骤：
          1. 触发 prompt:build:before hook（可替换 system / 追加 additional_context）
          2. 调用 sanitize_messages 修复 tool_use → tool_result 序列
          3. 触发 prompt:llm:input hook（观测最终输入，不修改）

        Returns:
            tuple[str, list[dict]]: (effective_system, effective_messages)
        """
        rt = self._rt
        from ccserver.messages import unified_message_to_wire

        # ① hook: prompt:build:before — 可修改 system/messages
        # 注意：传给 hook 的是消息的独立浅拷贝，避免行为不端的 hook 直接改到真实输出
        build_hook = await rt.session.hooks.emit(
            "prompt:build:before",
            {
                "system": rt.system,
                "messages": [unified_message_to_wire(m) for m in rt.context.messages],
                "model": rt.model,
            },
            rt._build_hook_ctx(),
        )
        # 断言：build:before hook 必须返回 HookResult，None 表示 hook 分发器损坏
        assert build_hook is not None, "prompt:build:before hook 必须返回 HookResult"
        # hook 可替换 system（build_hook.system_message 非空则使用，否则沿用原 system）
        effective_system = build_hook.system_message or rt.system
        # hook 可追加 additional_context 到最后一条 user 消息
        if build_hook.additional_context:
            msgs = [unified_message_to_wire(m) for m in rt.context.messages]
            if msgs and msgs[-1].get("role") == "user":
                # 最后一条 user 消息的 content 可能为 str（早期/命令）或 list[block]（S4 起恒为 list），两种都要处理
                last_content = msgs[-1].get("content", "")
                if isinstance(last_content, list):
                    # content 为块列表：追加一个 text 块
                    msgs[-1]["content"] = last_content + [{"type": "text", "text": build_hook.additional_context}]
                else:
                    # content 为字符串：直接拼接
                    msgs[-1]["content"] = (last_content or "") + "\n\n" + build_hook.additional_context
            effective_messages = msgs
        else:
            effective_messages = [unified_message_to_wire(m) for m in rt.context.messages]

        # ② 兜底净化：hook 可能破坏了 tool_use → tool_result 配对
        self.sanitize_messages(effective_messages)

        # ③ hook: prompt:llm:input — 观测即将发送给 LLM 的完整输入（不修改）
        await rt.session.hooks.emit_void(
            "prompt:llm:input",
            {"messages": effective_messages, "model": rt.model},
            rt._build_hook_ctx(),
        )

        return effective_system, effective_messages

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
