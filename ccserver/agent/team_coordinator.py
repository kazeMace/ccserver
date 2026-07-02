"""
agent.team_coordinator — Agent Team inbox 消息的消费与转换。

背景：
  Agent._loop() 每轮开始会非阻塞读取 context.inbox,处理来自 Team Mailbox /
  EventBus 订阅者 / Cron 调度器的消息(new_task / shutdown_request / chat /
  permission_response / scheduled_task_trigger 等),把它们转换为要追加到对话
  历史的消息,并告知 _loop 是否收到关闭请求。

设计：
  抽出 TeamCoordinator。这部分逻辑虽与 _loop 轮次节奏配合,但本身**无副作用**:
  它只读 rt.context.inbox,返回 (new_messages, shutdown_requested),由 _loop
  解读后决定追加消息 / 退出。因此可安全抽出,只依赖 AgentRuntime 契约。

  Agent 保留 _drain_inbox_and_respond 薄委托方法(_loop 调用),行为逐字一致。
"""

from __future__ import annotations

import asyncio

from loguru import logger

from ccserver.team.protocol import MsgType
from .runtime import AgentRuntime


class TeamCoordinator:
    """
    Team inbox 协调器,被 Agent 持有(组合)。

    依赖 AgentRuntime 提供 context(inbox)与 aid_label(日志)。
    """

    def __init__(self, rt: AgentRuntime):
        self._rt = rt

    async def drain_inbox_and_respond(self) -> tuple[list[dict], bool]:
        """
        非阻塞读取 inbox，处理 Agent Team 相关的 mailbox 消息
        （new_task, shutdown_request, chat 等）。

        进度事件改由 _loop() 每轮主动 publish 到 EventBus（推送模型），
        不再需要外部轮询注入 status_request。

        Returns:
            (需要追加到 messages 的新消息列表, 是否收到 shutdown_request)
        """
        rt = self._rt
        new_messages: list[dict] = []
        shutdown_requested = False

        # 消费 inbox 中的 Team Mailbox 消息
        while True:
            try:
                msg = rt.context.inbox.get_nowait()
            except asyncio.QueueEmpty:
                break

            # msg_type 字段标识来自 TeamMailboxPoller 或 EventBus 订阅者的消息
            match msg.get("type") or msg.get("msg_type"):
                case MsgType.NEW_TASK:
                    # 新任务：Team Lead 分配过来的任务，转为 user 消息追加到对话历史
                    new_messages.append({
                        "role": "user",
                        "content": msg.get("task_prompt", msg.get("text", "")),
                        "_ccserver_team_new_task": True,
                        "task_id": msg.get("task_id"),
                    })

                case MsgType.SHUTDOWN_REQUEST:
                    # 关闭请求：Team Lead 要求优雅退出，注入 system 消息让 LLM 总结后结束
                    new_messages.append({
                        "role": "system",
                        "content": "[Team Lead 请求你优雅退出，总结当前进度后结束。]",
                    })
                    shutdown_requested = True

                case MsgType.CHAT:
                    # 聊天消息：来自其他 Agent 的即时通信，附上发送方标识
                    new_messages.append({
                        "role": "user",
                        "content": f"[{msg.get('from_agent')}] {msg.get('text', '')}",
                    })

                case MsgType.PERMISSION_RESPONSE:
                    # P1-2：权限审批已统一走 EventBus PERMISSION_REQ 路径（内存 Future）。
                    # Mailbox 的 permission_response 路径已废弃，此 case 仅做兼容性保留：
                    # 如果有遗留消息进入 inbox，静默消费掉，防止堆积影响其他消息处理。
                    logger.debug(
                        "Inbox permission_response consumed (deprecated Mailbox path) | agent={} request_id={}",
                        rt.aid_label,
                        msg.get("request_id"),
                    )

                case MsgType.CRON_TRIGGER | MsgType.SCHEDULED_TASK_TRIGGER:
                    # 定时任务触发（兼容旧 cron_trigger 和新的 scheduled_task_trigger）
                    cron_prompt = msg.get("prompt", "")
                    new_messages.append({
                        "role": "user",
                        "content": cron_prompt,
                        "_ccserver_scheduled_task": True,
                        "task_id": msg.get("task_id"),
                        "trigger_type": msg.get("trigger_type", "cron"),
                    })
                    logger.debug(
                        "Inbox scheduled_task_trigger consumed | agent={} task_id={} type={}",
                        rt.aid_label,
                        msg.get("task_id"),
                        msg.get("trigger_type", "cron"),
                    )

                case _:
                    # 未知消息类型，记录警告但不中断循环
                    logger.warning(
                        "Inbox unknown msg type ignored | agent={} msg={}",
                        rt.aid_label,
                        msg,
                    )

        return new_messages, shutdown_requested
