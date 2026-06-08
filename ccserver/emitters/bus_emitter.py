"""
bus_emitter — 将 BaseEmitter 的 emit_*() 调用翻译为 AgentEvent 并 publish 到 EventBus。

职责
────
BusEmitter 是 Agent 和 EventBus 之间的翻译层：
  - Agent 内部只调用 self.emitter.emit_token() / emit_done() 等，不感知总线
  - BusEmitter 把这些调用翻译成统一的 AgentEvent 格式，publish 到 Session 级 EventBus
  - 所有订阅了 EventBus 的观察者（OutputTarget.Processor 等）独立收到事件副本

ask_user / permission_request 的 future 机制
────────────────────────────────────────────
  旧：BusEmitter.emit_ask_user() 直接返回 ""（不等待用户）。
  新：创建 asyncio.Future，把 future 放进事件 payload 一起 publish，
      OutputTarget.Processor 收到事件后调用 answer_cb(text)，
      answer_cb 把 future.set_result(text)，BusEmitter 返回答案，Agent 继续。

visibility 字段
──────────────
  BusEmitter 构造时接受 visibility 参数，所有 publish 的 AgentEvent 都带此字段。
  父 Agent spawn 子 Agent 时，根据可见性需求传入不同的 visibility。
"""

import asyncio
from typing import TYPE_CHECKING

from ccserver.emitters.base import BaseEmitter
from ccserver.event_bus import AgentEvent, EventBus, EventType, SenderType, _VISIBILITY_FULL

if TYPE_CHECKING:
    from ccserver.builtins.tools.base import ToolResult


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

    def __init__(
        self,
        bus: EventBus,
        agent_id: str,
        session_id: str,
        sender_type: str = SenderType.AGENT,
        visibility: str = _VISIBILITY_FULL,
    ):
        """
        Args:
            bus:         Session 级 EventBus 实例。
            agent_id:    发送方唯一 ID，作为事件的 agent_id 字段。
            session_id:  所属 Session 的 ID，作为事件的 session_id 字段。
            sender_type: 发送方类型，取值见 SenderType。默认 "agent"。
            visibility:  事件可见性，控制 OutputTarget.Processor 是否处理。
                         取值见 event_bus._VISIBILITY_* 常量。
                         父 Agent spawn 子 Agent 时，根据需要传入不同值。
        """
        self._bus = bus
        self._agent_id = agent_id
        self._session_id = session_id
        self._sender_type = sender_type
        self._visibility = visibility

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
            visibility=self._visibility,
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

    async def emit_tool_result_with_image(self, name: str, result: "ToolResult") -> None:
        """
        含图像的工具结果。

        向总线同时发布两个事件：
        1. TOOL_DONE — 文本描述（与普通工具结果格式一致）
        2. IMAGE — 图像数据（供 SSE/WS/Feishu 等渠道渲染图像）

        Args:
            name:   工具名称（通常是 "ScreenCapture"）。
            result: 多模态 ToolResult（has_image=True）。
        """
        # 1. 文本描述事件（与普通 emit_tool_result 保持格式一致）
        await self._bus.publish(
            self._make_event(
                EventType.TOOL_DONE,
                {"tool_name": name, "result": result.content_text[:500]},
            )
        )
        # 2. 图像事件（缩略图优先，无缩略图时用完整图像）
        img_b64 = result.get_thumbnail_base64() or result.get_image_base64()
        if img_b64:
            await self._bus.publish(
                self._make_event(
                    EventType.IMAGE,
                    {
                        "tool_name": name,
                        "image_base64": img_b64,
                        "description": result.content_text,
                    },
                )
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
        向用户提问，挂起等待 OutputTarget.Processor 注入答案。

        实现方式：
          1. 创建 asyncio.Future，把 future 放入事件 payload 一起 publish。
          2. 所有 OutputTarget.Processor 收到 ASK_USER 事件后，以各自方式向用户提问，
             用户回答后调 answer_cb(text)，answer_cb 调用 future.set_result(text)。
          3. 本方法 await future，返回用户答案，Agent 继续执行。

        超时 300 秒（5 分钟），超时后抛 asyncio.TimeoutError。

        Args:
            questions: 问题列表，每项含 question/options 等字段。

        Returns:
            用户回答的文本。
        """
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        await self._bus.publish(
            self._make_event(
                EventType.ASK_USER,
                {"questions": questions, "future": future},
            )
        )
        # 等待 Processor 通过 future.set_result() 注入答案
        return await asyncio.wait_for(asyncio.shield(future), timeout=300)

    async def emit_permission_request(
        self, tool_name: str, tool_input: dict, to_agent: str | None = None
    ) -> bool:
        """
        工具权限确认请求，挂起等待 OutputTarget.Processor 注入审批结果。

        实现方式与 emit_ask_user 相同：
          1. 创建 asyncio.Future，把 future 放入事件 payload 一起 publish。
          2. OutputTarget.Processor 收到事件后向用户展示审批请求，
             用户决定后调 grant_cb(True/False)，grant_cb 调用 future.set_result(bool)。
          3. 本方法 await future，返回审批结果。

        超时 300 秒，超时后默认返回 False（拒绝）。

        Args:
            tool_name:  工具名称。
            tool_input: 工具输入参数。
            to_agent:   目标 Agent ID（team 场景中指向 Lead），None 表示广播。

        Returns:
            True 表示批准，False 表示拒绝。
        """
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        await self._bus.publish(
            self._make_event(
                EventType.PERMISSION_REQ,
                {"tool_name": tool_name, "tool_input": tool_input, "future": future},
                to_agent=to_agent,
            )
        )
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=300)
        except asyncio.TimeoutError:
            # 超时默认拒绝，避免 Agent 永久挂起
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
