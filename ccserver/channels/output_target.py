"""
channels/output_target.py — OutputTarget 数据结构和 Visibility 枚举。

OutputTarget 是新出站架构的核心数据结构，绑定了：
  - 发给谁（channel_id / account_id / to）
  - 用什么 Processor 处理事件流
  - ask_user / permission_request 的 asyncio.Future（用于等待用户回答）

每次 dispatch_inbound() 时，Gateway 根据路由配置组装 OutputTarget 列表，
挂到 Session.output_targets 上，由 EventBus 订阅者循环驱动 Processor 消费事件。
"""

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .processor import Processor


# ── Visibility 枚举 ────────────────────────────────────────────────────────────


class Visibility(str, Enum):
    """
    AgentEvent 的可见性等级，控制 Processor 是否处理该事件。

    打标时机：Agent spawn 时由父 Agent 决定，注入到 BusEmitter，
    并设置到 AgentEvent.visibility 字段。

    Values:
        FULL      : 全量可见，root Agent 或用户显式创建的 background agent 使用。
                    Processor 处理所有事件（token / done / error / ask_user）。
        DONE_ONLY : 只有 done/error 对用户可见，普通子 agent 默认使用。
                    Processor 忽略 token 事件，只处理 done 和 error。
        HIDDEN    : 完全不可见，内部工具调用型子 agent 使用。
                    Processor 丢弃所有事件，不发送任何内容给用户。
    """
    FULL      = "full"
    DONE_ONLY = "done_only"
    HIDDEN    = "hidden"


# ── OutputTarget ──────────────────────────────────────────────────────────────


@dataclass
class OutputTarget:
    """
    出站目标：绑定"发给谁"和"用什么 Processor 处理事件流"。

    每次 dispatch_inbound() 时，Gateway 为每个目标 channel 创建一个 OutputTarget 实例，
    并挂到 Session.output_targets 上。EventBus 订阅者循环从总线取事件后，
    遍历 output_targets，调用各自 Processor 的 on_* 方法。

    Attributes:
        channel_id:         目标 channel 标识，如 "feishu"、"discord"、"webchat"。
        account_id:         用哪个账号发送（多账号场景下区分不同 bot）。
        to:                 目标用户/群组 ID（平台特定）。
        reply_to_id:        回复时引用的消息 ID（可选，平台特定）。
        processor:          事件流处理器实例，负责把 AgentEvent 转化为实际发送动作。
        answer_future:      ask_user 等待 future。BusEmitter.emit_ask_user() 挂起时
                            把 future 传给 Processor，Processor 在用户回答后 set_result()。
        permission_future:  permission_request 等待 future，与 answer_future 类似。
    """
    channel_id: str
    account_id: str
    to: str
    reply_to_id: Optional[str]
    processor: "Processor"
    answer_future: Optional[asyncio.Future] = field(default=None, compare=False)
    permission_future: Optional[asyncio.Future] = field(default=None, compare=False)
