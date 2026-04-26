"""
bus_emitter — 将 BaseEmitter 的 emit_*() 调用翻译为 AgentEvent 并 publish 到 EventBus。

职责
────
BusEmitter 是 Agent 和 EventBus 之间的翻译层：
  - Agent 内部只调用 self.emitter.emit_token() / emit_done() 等，不感知总线
  - BusEmitter 把这些调用翻译成统一的 AgentEvent 格式，publish 到 Session 级 EventBus
  - 所有订阅了 EventBus 的观察者（SSE、父 Agent、Recorder 等）独立收到事件副本

替换关系
────────
  旧：child.emitter = QueueEmitter(outbox)  # 写入一个固定队列，只有一个消费者
  新：child.emitter = BusEmitter(bus, agent_id, session_id)  # 广播给所有订阅者

Agent 内部代码零改动，只替换注入的 emitter 实例。
"""

import asyncio

from loguru import logger

from ccserver.emitters.base import BaseEmitter
from ccserver.event_bus import AgentEvent, EventBus, EventType, SenderType


class BusEmitter(BaseEmitter):
    """
    把 emit_*() 调用翻译为 AgentEvent 并 publish 到 EventBus。

    Args:
        bus:         Session 级 EventBus 实例。
        agent_id:    发送方唯一 ID，作为事件的 agent_id 字段。
        session_id:  所属 Session 的 ID，作为事件的 session_id 字段。
        sender_type: 发送方类型，取值见 SenderType。默认 "agent"，
                     Gateway 等非 Agent 组件应传入对应类型。

    使用方式：
        emitter = BusEmitter(session.event_bus, agent_id=child.context.agent_id, session_id=session.id)
        child = Agent(..., emitter=emitter, ...)
    """

    def __init__(self, bus: EventBus, agent_id: str, session_id: str, sender_type: str = SenderType.AGENT):
        self._bus = bus
        self._agent_id = agent_id
        self._session_id = session_id
        self._sender_type = sender_type

    def _make_event(
        self,
        event_type: str,
        payload: dict,
        to_agent: str | None = None,
    ) -> AgentEvent:
        """
        构造 AgentEvent 的辅助方法。

        Args:
            event_type: 事件类型，取值见 EventType。
            payload:    事件内容字典。
            to_agent:   目标 Agent ID，None 表示广播。
        """
        return AgentEvent(
            type=event_type,
            agent_id=self._agent_id,
            session_id=self._session_id,
            payload=payload,
            sender_type=self._sender_type,
            to_agent=to_agent,
        )

    async def emit(self, event: dict) -> None:
        """
        BaseEmitter 抽象方法实现。

        Agent 内部有些地方直接调用 self.emitter.emit({"type": "...", ...})，
        这里将原始 dict 包装成 AgentEvent 转发到总线。

        Args:
            event: 原始事件字典，必须包含 "type" 字段。
        """
        event_type = event.get("type", "unknown")
        # 把原始 dict 整体作为 payload，保留所有字段
        payload = {k: v for k, v in event.items() if k != "type"}
        await self._bus.publish(self._make_event(event_type, payload))

    async def emit_token(self, text: str) -> None:
        """
        LLM 流式输出一个 token 片段。

        Args:
            text: token 文本内容。

        publish payload: {"token": text}
        """
        await self._bus.publish(
            self._make_event(EventType.TOKEN, {"token": text})
        )

    async def emit_tool_start(self, name: str, preview: str) -> None:
        """
        工具开始执行。

        Args:
            name:    工具名称（如 "Bash"、"Read"）。
            preview: 工具输入的人类可读摘要。

        publish payload: {"tool_name": name, "preview": preview}
        """
        await self._bus.publish(
            self._make_event(EventType.TOOL_START, {"tool_name": name, "preview": preview})
        )

    async def emit_tool_result(self, name: str, output: str) -> None:
        """
        工具执行完毕。

        Args:
            name:   工具名称。
            output: 工具输出结果（截断到 500 字符）。

        publish payload: {"tool_name": name, "result": output}
        """
        await self._bus.publish(
            self._make_event(EventType.TOOL_DONE, {"tool_name": name, "result": output[:500]})
        )

    async def emit_done(self, content: str) -> None:
        """
        Agent 任务完成。

        Args:
            content: Agent 的最终输出内容。

        publish payload: {"content": content}
        """
        await self._bus.publish(
            self._make_event(EventType.DONE, {"content": content})
        )

    async def emit_error(self, message: str) -> None:
        """
        Agent 出错。

        Args:
            message: 错误信息。

        publish payload: {"error": message}
        """
        await self._bus.publish(
            self._make_event(EventType.ERROR, {"error": message})
        )

    async def emit_subagent_done(self, content: str) -> None:
        """
        子 Agent 完成（父 Agent 层面的通知）。
        复用 DONE 事件类型，payload 加 subagent=True 标记以便区分。

        Args:
            content: 子 Agent 的最终输出。
        """
        await self._bus.publish(
            self._make_event(EventType.DONE, {"content": content, "subagent": True})
        )

    async def emit_compact(self, reason: str) -> None:
        """
        消息压缩通知（上下文窗口触达压缩阈值）。

        Args:
            reason: 压缩原因描述。

        publish payload: {"reason": reason}
        """
        await self._bus.publish(
            self._make_event("compact", {"reason": reason})
        )

    async def emit_ask_user(self, questions: list) -> str:
        """
        向用户提问。

        BusEmitter 用于子 Agent（非交互模式），不支持等待用户回答。
        只 publish 事件，立即返回空字符串。

        Args:
            questions: 问题列表。

        Returns:
            空字符串（不支持交互）。
        """
        await self._bus.publish(
            self._make_event("ask_user", {"questions": questions})
        )
        return ""

    async def emit_permission_request(
        self, tool_name: str, tool_input: dict, to_agent: str | None = None
    ) -> bool:
        """
        工具权限确认请求。

        BusEmitter 用于子 Agent（非交互模式），直接返回 False（拒绝）。
        只 publish 事件，不等待用户响应。

        Args:
            tool_name:  工具名称。
            tool_input: 工具输入参数。
            to_agent:   目标 Agent ID（team 场景中指向 Lead），None 表示广播。

        Returns:
            False（不支持交互）。
        """
        await self._bus.publish(
            self._make_event(
                EventType.PERMISSION_REQ,
                {"tool_name": tool_name, "tool_input": tool_input},
                to_agent=to_agent,
            )
        )
        return False

    async def emit_task_started(
        self,
        task_id: str,
        task_type: str,
        description: str = "",
        pid: int | None = None,
    ) -> None:
        """
        后台任务启动通知。

        Args:
            task_id:     任务唯一 ID。
            task_type:   任务类型（"local_agent" / "local_bash"）。
            description: 人类可读描述。
            pid:         进程 ID（agent 任务为 None）。

        publish payload: {"task_id": ..., "task_type": ..., "description": ..., "pid": ...}
        """
        await self._bus.publish(
            self._make_event(
                "task_started",
                {
                    "task_id": task_id,
                    "task_type": task_type,
                    "description": description,
                    "pid": pid,
                },
            )
        )

    async def emit_task_progress(
        self,
        task_id: str,
        status: str,
        output: str = "",
        progress: dict | None = None,
    ) -> None:
        """
        后台任务进度通知。

        Args:
            task_id:  任务 ID。
            status:   当前状态（"running"）。
            output:   增量输出。
            progress: 进度信息（round_num / max_rounds / phase 等）。
        """
        await self._bus.publish(
            self._make_event(
                EventType.PROGRESS,
                {
                    "task_id": task_id,
                    "status": status,
                    "output": output[:2000] if output else "",
                    "progress": progress,
                },
            )
        )

    async def emit_task_done(
        self,
        task_id: str,
        status: str,
        output: str = "",
        exit_code: int | None = None,
        reason: str | None = None,
    ) -> None:
        """
        后台任务完成通知。

        Args:
            task_id:   任务 ID。
            status:    最终状态（"completed" / "failed" / "cancelled"）。
            output:    完整输出（截断到 50KB）。
            exit_code: 退出码（agent 任务为 None）。
            reason:    失败原因（失败时填写）。
        """
        await self._bus.publish(
            self._make_event(
                "task_done",
                {
                    "task_id": task_id,
                    "status": status,
                    "output": output[:50_000] if output else "",
                    "exit_code": exit_code,
                    "reason": reason,
                },
            )
        )
