"""
agent.tool_dispatcher — 工具调用的路由与执行。

背景：
  Agent._loop() 每轮拿到 LLM 响应后,需要执行其中的 tool_use 块:权限检查、
  hook 触发、分派到内置工具/Agent/SendMessage/AskUser/MCP、多模态图像路由、
  并行 Agent 调用等。这部分逻辑(_handle_tools 及一组 _handle_* )占了 Agent
  最大的篇幅。

设计：
  抽出 ToolDispatcher,持有 AgentRuntime,通过 rt.* 访问父 Agent 状态。
  派生类工具(Agent / teammate / background)仍委托回 rt.spawn_*,
  保持与重构前一致的调用链(含外部 monkeypatch 生效)。
  ToolDispatcher.handle(blocks) 返回 (tool_results, trigger_compact),
  行为与原 Agent._handle_tools 逐字一致。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from loguru import logger

from ..config import MAX_DEPTH
from ..event_bus import AgentEvent, EventType, SenderType
from ..utils import get_block_attr
from ccserver.builtins.tools import ToolResult
from ccserver.team.mailbox import TeamMailbox
from ccserver.team.protocol import ChatMessage
from ccserver.team.helpers import format_agent_id
from .runtime import AgentRuntime


class ToolDispatcher:
    """
    工具分发器,被 Agent 持有(组合)。

    依赖 AgentRuntime 提供 session/state/emitter/adapter/tools/context 等,
    以及父 Agent 的 spawn_child / spawn_background / _spawn_teammate 委托方法。
    """

    def __init__(self, rt: AgentRuntime):
        self._rt = rt

    async def handle(self, blocks) -> tuple[list[dict], bool]:
        """
        执行响应中所有 tool_use 块。
        返回 (用于 API 的 tool_result 列表, trigger_compact 标志)。

        权限检查（在工具执行前）：
          如果工具名在 settings.ask_tools 中，则根据 run_mode 决定：
            auto        — 直接拒绝（返回错误结果，不执行工具）
            interactive — 推送 permission_request 事件等待用户批准；拒绝则同 auto
        """
        rt = self._rt
        results: list[dict] = []
        trigger_compact = False
        ask_tools = rt.session.settings.ask_tools

        # 扫描是否有多个 Agent 工具调用，决定是否启用并行模式
        _agent_count = 0
        for _block in blocks:
            if get_block_attr(_block, "type") == "tool_use":
                _name = get_block_attr(_block, "name") or ""
                if _name == "Agent":
                    _agent_count += 1
        parallel_agent_mode = _agent_count > 1

        # 收集 Agent 工具的异步调用信息（仅在并行模式下使用）
        agent_tasks: list[tuple[int, str, dict, str, datetime, asyncio.Task]] = []

        for block in blocks:
            if get_block_attr(block, "type") != "tool_use":
                continue

            name: str = get_block_attr(block, "name") or ""
            input_: dict = get_block_attr(block, "input") or {}
            block_id: str = get_block_attr(block, "id") or ""

            # ── 运行时权限检查 ────────────────────────────────────────────────
            if name in ask_tools:
                perm_hook = await rt.session.hooks.emit(
                    "tool:permission:request",
                    {"tool_name": name, "tool_input": input_, "tool_use_id": block_id},
                    rt._build_hook_ctx(),
                )

                if perm_hook.block:
                    logger.info("Hook blocked permission | agent={} tool={} reason={}", rt.aid_label, name, perm_hook.block_reason)
                    result = ToolResult.error(perm_hook.block_reason or f"Tool '{name}' blocked by permission hook.")
                    results.append(result.to_api_dict(block_id))
                    continue

                behavior = perm_hook.permission_behavior
                if behavior == "allow":
                    logger.info("Hook allowed permission | agent={} tool={}", rt.aid_label, name)
                elif behavior in ("deny",):
                    logger.info("Hook denied permission | agent={} tool={}", rt.aid_label, name)
                    result = ToolResult.error(f"Tool '{name}' denied by permission hook.")
                    results.append(result.to_api_dict(block_id))
                    continue
                elif behavior in ("ask", "passthrough"):
                    if rt.run_mode == "interactive":
                        logger.info("Permission request | agent={} tool={} mode=interactive", rt.aid_label, name)
                        granted = await rt.emitter.emit_permission_request(name, input_)
                        if not granted:
                            logger.info("Permission denied  | agent={} tool={}", rt.aid_label, name)
                            await rt.session.hooks.emit_void(
                                "tool:permission:denied",
                                {"tool_name": name, "tool_input": input_, "tool_use_id": block_id, "reason": "user_denied"},
                                rt._build_hook_ctx(),
                            )
                            result = ToolResult.error(f"Tool '{name}' was denied by user.")
                            results.append(result.to_api_dict(block_id))
                            continue
                        logger.info("Permission granted | agent={} tool={}", rt.aid_label, name)
                    else:
                        logger.info("Permission denied (auto) | agent={} tool={}", rt.aid_label, name)
                        await rt.session.hooks.emit_void(
                            "tool:permission:denied",
                            {"tool_name": name, "tool_input": input_, "tool_use_id": block_id, "reason": "auto_mode"},
                            rt._build_hook_ctx(),
                        )
                        result = ToolResult.error(
                            f"Tool '{name}' requires user confirmation but run_mode is 'auto'. "
                            "Add it to permissions.ask and use interactive mode, or remove it from ask_tools."
                        )
                        results.append(result.to_api_dict(block_id))
                        continue

            # hook: tool:call:before — 工具执行前（modifying，可阻断、可修改输入）
            tool_hook = await rt.session.hooks.emit(
                "tool:call:before",
                {"tool_name": name, "tool_input": input_, "tool_use_id": block_id},
                rt._build_hook_ctx(),
            )
            if tool_hook.block:
                logger.info("Hook blocked tool | agent={} tool={} reason={}", rt.aid_label, name, tool_hook.block_reason)
                result = ToolResult.error(tool_hook.block_reason or f"Tool '{name}' blocked by hook.")
                results.append(result.to_api_dict(block_id))
                continue
            if tool_hook.updated_input is not None:
                logger.debug("Hook updated tool input | agent={} tool={}", rt.aid_label, name)
                input_ = tool_hook.updated_input

            if name.startswith("mcp__"):
                preview_parts = [f"{k}={str(v)[:200]}" for k, v in input_.items()]
                preview = ", ".join(preview_parts)
            else:
                preview = str(list(input_.values())[0])[:80] if input_ else ""

            tool_start_ts = datetime.now(timezone.utc)
            rt.state.current_tool = name

            await rt.emitter.emit_tool_start(name, preview)

            if name == "Agent":
                if parallel_agent_mode:
                    task = asyncio.create_task(self._handle_agent(input_))
                    agent_tasks.append((len(results), block_id, input_, preview, tool_start_ts, task))
                    results.append(None)  # 预占位
                    continue
                result = await self._handle_agent(input_)
            elif name == "SendMessage":
                result = await self._handle_send_message(input_)
            elif name == "AskUserQuestion":
                result = await self._handle_ask_user(input_)
            elif name == "Compact":
                trigger_compact = True
                result = ToolResult.ok("Compressing...")
            elif name.startswith("mcp__"):
                result = await self._handle_mcp_tool(name, input_, block_id)
            else:
                tool = rt.tools.get(name)
                if tool:
                    logger.debug("Tool call  | agent={} tool={} input={}", rt.aid_label, name, input_)
                    result = await tool(**input_)
                    logger.debug("Tool result| agent={} tool={} result={!r}", rt.aid_label, name, result.content_text[:200] if result.content_text else "")
                else:
                    logger.warning("Unknown tool | agent={} tool={}", rt.aid_label, name)
                    result = ToolResult.error(f"Unknown tool: {name}")

            tool_duration_ms = int((datetime.now(timezone.utc) - tool_start_ts).total_seconds() * 1000)
            rt.state.current_tool = None

            # ── 多模态图像路由（NATIVE vs TRANSCRIBE）────────────────────────
            if result.has_image:
                can_native = (
                    rt.adapter.supports_image
                    and rt.adapter.supports_image_in_tool_result
                )
                if not can_native:
                    result = await self._transcribe_image_result(result, name)

            if result.has_image:
                await rt.emitter.emit_tool_result_with_image(name, result)
            else:
                await rt.emitter.emit_tool_result(name, result.content_text)

            await rt.session.event_bus.publish(AgentEvent(
                type=EventType.TOOL_DONE,
                agent_id=rt.context.agent_id,
                session_id=rt.session.id,
                sender_type=SenderType.AGENT,
                payload={
                    "tool_name": name,
                    "tool_use_id": block_id,
                    "duration_ms": tool_duration_ms,
                    "is_error": result.is_error,
                    "result_preview": result.content_text[:200],
                    "tool_input_preview": preview,
                    "has_image": result.has_image,
                },
            ))
            if result.is_error:
                await rt.session.hooks.emit_void(
                    "tool:call:failure",
                    {"tool_name": name, "tool_use_id": block_id, "tool_input": input_, "error": result.content_text or ""},
                    rt._build_hook_ctx(),
                )
            else:
                await rt.session.hooks.emit_void(
                    "tool:call:after",
                    {"tool_name": name, "tool_use_id": block_id, "tool_input": input_, "tool_response": result.content_text or ""},
                    rt._build_hook_ctx(),
                )
            await rt.session.hooks.emit_void(
                "tool:result:persist",
                {
                    "tool_name": name,
                    "tool_use_id": block_id,
                    "tool_input": input_,
                    "is_error": result.is_error,
                    "result_text": result.content_text or "",
                    "has_image": result.has_image,
                },
                rt._build_hook_ctx(),
            )
            results.append(result.to_api_dict(block_id))

        # ── 并行等待所有 Agent 工具完成（仅当存在多个 Agent 调用时）──
        if agent_tasks:
            if len(agent_tasks) > 1:
                agent_results = await asyncio.gather(
                    *[task for _, _, _, _, _, task in agent_tasks],
                    return_exceptions=True,
                )
            else:
                _, _, _, _, _, task = agent_tasks[0]
                try:
                    agent_results = [await task]
                except Exception as e:
                    agent_results = [e]

            for (idx, bid, inp, preview, ts, _), res in zip(agent_tasks, agent_results):
                if isinstance(res, Exception):
                    logger.error("Agent tool parallel execution failed | error={}", res)
                    result = ToolResult.error(str(res))
                else:
                    result = res

                tool_duration_ms = int((datetime.now(timezone.utc) - ts).total_seconds() * 1000)
                rt.state.current_tool = None

                await rt.emitter.emit_tool_result("Agent", result.content_text)

                await rt.session.event_bus.publish(AgentEvent(
                    type=EventType.TOOL_DONE,
                    agent_id=rt.context.agent_id,
                    session_id=rt.session.id,
                    sender_type=SenderType.AGENT,
                    payload={
                        "tool_name": "Agent",
                        "tool_use_id": bid,
                        "duration_ms": tool_duration_ms,
                        "is_error": result.is_error,
                        "result_preview": result.content_text[:200],
                        "tool_input_preview": preview,
                    },
                ))

                if result.is_error:
                    await rt.session.hooks.emit_void(
                        "tool:call:failure",
                        {"tool_name": "Agent", "tool_use_id": bid, "tool_input": inp, "error": result.content_text or ""},
                        rt._build_hook_ctx(),
                    )
                else:
                    await rt.session.hooks.emit_void(
                        "tool:call:after",
                        {"tool_name": "Agent", "tool_use_id": bid, "tool_input": inp, "tool_response": result.content_text or ""},
                        rt._build_hook_ctx(),
                    )

                results[idx] = result.to_api_dict(bid)

        return results, trigger_compact

    # ── 多模态图像转写 ──────────────────────────────────────────────────────────

    async def _transcribe_image_result(self, result: "ToolResult", tool_name: str) -> "ToolResult":
        """
        TRANSCRIBE 路径：将图像 tool_result 中的图像转换为文字描述。

        当主模型不支持图像或 endpoint 不支持图像 tool_result 时调用。
        使用 VLMRouter 选择最佳视觉模型进行描述，VLM 不可用则返回占位文字。
        """
        from ccserver.model.routing.router import VLMRouter
        from ccserver.model.media.describe import describe_image_with_model

        rt = self._rt
        image_base64 = result.get_image_base64()
        if not image_base64:
            logger.warning("TRANSCRIBE: 无法提取图像数据 | tool={}", tool_name)
            return ToolResult.ok(result.content_text or "[图像无法显示]")

        try:
            router = VLMRouter(
                main_model=rt.model,
                main_adapter=rt.adapter,
            )
            route = await router.route()

            if route.is_native:
                vlm_adapter = route.adapter
                vlm_model = route.model
            else:
                vlm_adapter = route.adapter
                vlm_model = route.model

            logger.info(
                "TRANSCRIBE 图像描述 | tool={} vlm_model={} vlm_provider={}",
                tool_name, vlm_model, route.provider_id,
            )

            description = await describe_image_with_model(
                image_base64=image_base64,
                adapter=vlm_adapter,
                model=vlm_model,
                max_tokens=1000,
            )

            existing_text = result.content_text
            if existing_text and existing_text != "[multimodal content]":
                combined = f"{existing_text}\n\n[图像内容描述]\n{description}"
            else:
                combined = f"[图像内容描述]\n{description}"

            return ToolResult.ok(combined)

        except Exception as e:
            logger.warning("TRANSCRIBE 失败，使用占位文字 | tool={} error={}", tool_name, e)
            existing_text = result.content_text
            return ToolResult.ok(existing_text or "[图像无法显示：VLM 不可用]")

    # ── Agent 工具（派生子代理）──────────────────────────────────────────────────

    async def _handle_agent(self, task_input: dict) -> ToolResult:
        """
        派生子代理并运行。

        根据 run_in_background 参数分为两条路径：
          - run_in_background=False（默认）：await child._loop() 阻塞等待，返回摘要。
          - run_in_background=True        ：调用 spawn_background() 立即返回 task_id。

        派生委托回父 Agent 的 spawn_*（rt.spawn_child / rt.spawn_background /
        rt._spawn_teammate），保持调用链不变。
        """
        rt = self._rt
        if rt.context.depth >= MAX_DEPTH:
            logger.warning("Max depth reached | agent={} depth={}", rt.aid_label, rt.context.depth)
            return ToolResult.error(
                f"Max agent nesting depth ({MAX_DEPTH}) reached. "
                "Cannot spawn further subagents."
            )
        prompt = task_input.get("prompt", "")
        if not prompt:
            return ToolResult.error("Task requires a non-empty prompt.")

        subagent_type = task_input.get("subagent_type", "")
        agent_name = subagent_type or task_input.get("description", "")
        model_override = task_input.get("model", "") or None
        run_in_background = bool(task_input.get("run_in_background", False))
        team_name = task_input.get("team_name", "")
        teammate_name = task_input.get("name", "")
        logger.info(
            "Agent tool called  | parent={} subagent_type={} description={} "
            "model={} run_in_background={} team_name={} teammate_name={}",
            rt.aid_label, subagent_type or "(generic)", agent_name or "-",
            model_override or "inherit", run_in_background,
            team_name or "-", teammate_name or "-",
        )
        agent_def = rt.session.agents.get(subagent_type) if subagent_type else None
        if subagent_type and agent_def is None:
            logger.warning("Agent def not found | subagent_type={}", subagent_type)

        _persistent_param = task_input.get("persistent")
        if _persistent_param is not None:
            is_persistent = bool(_persistent_param)
        elif agent_def is not None:
            is_persistent = agent_def.is_persistent
        else:
            is_persistent = False

        # ── Team 分支 ──
        if team_name and teammate_name and rt.session.settings.user_agent_team:
            if agent_def and not agent_def.is_team_capable:
                return ToolResult.error(
                    f"Agent '{subagent_type}' is not team-capable. "
                    f"Set is_team_capable=true in its frontmatter."
                )
            try:
                handle = await rt._spawn_teammate(
                    team_name=team_name,
                    name=teammate_name,
                    prompt=prompt,
                    agent_def=agent_def,
                    model_override=model_override,
                )
            except Exception as e:
                logger.error("Spawn teammate failed | error={}", e)
                return ToolResult.error(f"Failed to spawn teammate: {e}")
            return ToolResult.ok(
                f"Teammate '{teammate_name}' spawned in team '{team_name}' (task_id={handle.agent_task_id})"
            )

        # hook: subagent:spawning — 子代理即将启动（observing）
        await rt.session.hooks.emit_void(
            "subagent:spawning",
            {},
            {"agent_id": rt.context.agent_id, "depth": rt.context.depth},
        )

        # ── 后台模式：spawn_background() 立即返回 ───────────────────────────
        if run_in_background:
            handle = rt.spawn_background(
                prompt=prompt,
                agent_def=agent_def,
                agent_name=agent_name,
                model_override=model_override,
                task_id=None,
                is_persistent=is_persistent,
            )
            logger.info(
                "Agent background  | parent={} agent_task_id={} agent_id={}",
                rt.aid_label, handle.agent_task_id, handle.agent_id[:8]
            )
            return ToolResult.ok(
                f"Agent started in background (agent_task_id={handle.agent_task_id})"
            )

        # ── 同步模式：spawn_child + _loop() 阻塞等待 ───────────────────────
        child = rt.spawn_child(
            prompt, agent_def=agent_def, agent_name=agent_name, model_override=model_override
        )
        agent_type_label = (
            f"{subagent_type}(defined)" if agent_def
            else f"{subagent_type}(undefined)" if subagent_type
            else "(generic)"
        )
        logger.info(
            "Child agent spawned | parent={} child={} depth={} type={}",
            rt.aid_label, child.aid_label, child.context.depth, agent_type_label
        )
        await rt.session.hooks.emit_void(
            "subagent:spawned",
            {"subagent_id": child.context.agent_id, "subagent_name": child.context.name or ""},
            child._build_hook_ctx(),
        )

        from ccserver.tasks import AgentTaskState, generate_agent_id, AgentTaskStatus
        sync_task_id = generate_agent_id()
        sync_task = AgentTaskState(
            id=sync_task_id,
            agent_id=child.context.agent_id,
            agent_name=child.context.name or "unnamed",
            description=f"[Agent] {child.context.name or 'unnamed'}: {prompt[:80]}",
            prompt=prompt,
            parent_id=child.context.parent_id,
            is_persistent=is_persistent,
        )
        child.context.agent_task_id = sync_task_id
        rt.session.agent_tasks.register(sync_task)
        logger.info(
            "Sync agent registered | parent={} agent_task_id={} agent_id={}",
            rt.aid_label, sync_task_id, child.context.agent_id[:8]
        )

        try:
            sync_task.status = AgentTaskStatus.RUNNING
            sync_task.start_time = datetime.now(timezone.utc)
            summary = await child._loop()
            sync_task.status = AgentTaskStatus.COMPLETED
            sync_task.result = summary
        except Exception as e:
            sync_task.status = AgentTaskStatus.FAILED
            sync_task.error = str(e)
            raise
        finally:
            sync_task.end_time = datetime.now(timezone.utc)
            if not sync_task.is_persistent:
                rt.session.agent_tasks.evict(sync_task_id)

        logger.info(
            "Child agent done   | child={} summary_len={}",
            child.aid_label, len(summary)
        )
        await rt.session.hooks.emit_void(
            "subagent:ended",
            {"summary": summary, "subagent_id": child.context.agent_id},
            child._build_hook_ctx(),
        )
        return ToolResult.ok(summary or "(no summary)")

    # ── SendMessage 工具（Team 内通信）──────────────────────────────────────────

    async def _handle_send_message(self, input_: dict) -> ToolResult:
        """
        处理 SendMessage 工具调用，将消息写入目标 teammate 的 Mailbox。

        仅当当前 Agent 属于某个已激活的 Team 时才允许调用。
        """
        rt = self._rt
        to = input_.get("to", "")
        message = input_.get("message", "")
        summary = input_.get("summary", "")

        if not to or not message:
            return ToolResult.error("SendMessage requires 'to' and 'message' parameters.")

        # 检查当前 agent 是否属于某个 team(_team_name 由 _spawn_teammate 设置在父 Agent 上)
        team_name = getattr(rt, "_team_name", None)
        if not team_name:
            return ToolResult.error(
                "SendMessage is only available for agents running inside a team."
            )

        registry = rt.session.team_registry
        if registry is None:
            return ToolResult.error("Team feature is not enabled.")

        team = registry.get_team(team_name)
        if team is None:
            return ToolResult.error(f"Team '{team_name}' not found.")

        mailbox = TeamMailbox(team_name, rt.session.storage)
        from_agent = rt.context.agent_id

        if to == "*":
            recipients = [m.agent_id for m in team.members.values() if m.agent_id != from_agent]
            chat_msg = ChatMessage(
                from_agent=from_agent,
                to_agent="*",
                text=message,
                summary=summary or None,
            )
            await mailbox.broadcast(chat_msg, recipients=recipients, exclude=from_agent)
            logger.info(
                "SendMessage broadcast | agent={} team={} recipients={}",
                rt.aid_label, team_name, len(recipients)
            )
            return ToolResult.ok(f"Message broadcast to {len(recipients)} teammate(s).")
        else:
            to_agent = format_agent_id(to, team_name)
            if to_agent not in team.members:
                return ToolResult.error(f"Teammate '{to}' not found in team '{team_name}'.")
            chat_msg = ChatMessage(
                from_agent=from_agent,
                to_agent=to_agent,
                text=message,
                summary=summary or None,
            )
            await mailbox.send(chat_msg)
            logger.info(
                "SendMessage sent | agent={} team={} to={}",
                rt.aid_label, team_name, to_agent
            )
            return ToolResult.ok(f"Message sent to {to}.")

    # ── AskUserQuestion 工具 ─────────────────────────────────────────────────────

    async def _handle_ask_user(self, input_: dict) -> ToolResult:
        """
        通过 emitter 向客户端推送提问，等待用户回答后返回答案。
        """
        rt = self._rt
        questions = input_.get("questions", [])
        if not questions:
            return ToolResult.error("AskUserQuestion requires at least one question.")

        logger.info("AskUserQuestion | agent={} questions={}", rt.aid_label, len(questions))
        answer = await rt.emitter.emit_ask_user(questions)
        logger.info("AskUserQuestion answered | agent={} answer_len={}", rt.aid_label, len(answer))

        return ToolResult.ok(answer if answer else "(user did not answer)")

    # ── MCP 工具转发 ─────────────────────────────────────────────────────────────

    async def _handle_mcp_tool(self, name: str, input_: dict, block_id: str) -> ToolResult:
        """转发 mcp__<server>__<tool> 调用到对应的 MCP server。"""
        rt = self._rt
        parts = name.split("__", 2)
        if len(parts) != 3:
            return ToolResult.error(f"Invalid MCP tool name: {name}")
        _, server_name, tool_name = parts
        client = rt.session.mcp.get_client(server_name)
        if client is None:
            return ToolResult.error(f"MCP server not found: {server_name}")
        logger.debug("MCP call | agent={} server={} tool={} input={}", rt.aid_label, server_name, tool_name, input_)
        outcome = await client.call(tool_name, input_)
        if outcome.is_error:
            logger.error(
                "MCP tool failed | agent={} server={} tool={} error={}",
                rt.aid_label, server_name, tool_name, outcome.content,
            )
            await rt.session.hooks.emit_void(
                "mcp:call:failure",
                {
                    "server": server_name,
                    "tool": tool_name,
                    "tool_use_id": block_id,
                    "tool_input": input_,
                    "error": outcome.content,
                },
                rt._build_hook_ctx(),
            )
            return ToolResult.error(outcome.content)
        return ToolResult.ok(outcome.content)
