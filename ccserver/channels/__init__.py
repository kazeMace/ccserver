"""
channels — CCServer Channel 系统。

设计目标
────────
- 与 OpenClaw 的 Channel 架构概念对齐，让熟悉 OpenClaw 的开发者能快速上手
- 所有外部平台（WebChat、Discord、Telegram、飞书、钉钉等）统一通过 ChannelAdapter 接入
- Session 级路由：自动记录"消息来自哪个 channel"，回复自动路由回原 channel
- 懒加载注册表：启动时不初始化连接，按需启动

核心模块
────────
- base       : BaseChannelAdapter + 核心数据类型（InboundMessage / OutboundMessage）
- registry   : ChannelRegistry，channel 适配器的注册与发现
- gateway    : ChannelGateway，统一消息路由与生命周期管理
- adapters   : 各平台适配器实现

典型用法
────────
# 1. 创建注册表和网关
registry = ChannelRegistry()
gateway = ChannelGateway(registry, session_manager)

# 2. 注册适配器
from ccserver.channels.adapters.webchat import WebChatAdapter
registry.register(WebChatAdapter)

# 3. 启动 channel
gateway.start_channel("webchat", "default", {})

# 4. 入站消息（由适配器调用）
await gateway.dispatch_inbound(InboundMessage(
    channel_id="webchat", account_id="default",
    sender_id="user1", text="hello", chat_type="direct",
))

# 5. 出站回复（由 Agent 事件触发）
await gateway.dispatch_outbound(session_id, text="hi there")
"""

from .base import (
    BaseChannelAdapter,
    ChannelCapabilities,
    ChannelAccountSnapshot,
    InboundMessage,
    OutboundMessage,
    ChatType,
)
from .registry import ChannelRegistry
from .gateway import ChannelGateway
from .lifecycle import ChannelLifecycle
from .processor_loop import ProcessorLoopManager
from .outbound import OutboundDispatcher
from .health_monitor import ChannelHealthMonitor
from .config import ChannelConfig

__all__ = [
    "BaseChannelAdapter",
    "ChannelCapabilities",
    "ChannelAccountSnapshot",
    "InboundMessage",
    "OutboundMessage",
    "ChatType",
    "ChannelRegistry",
    "ChannelGateway",
    "ChannelLifecycle",
    "ProcessorLoopManager",
    "OutboundDispatcher",
    "ChannelHealthMonitor",
    "ChannelConfig",
]
