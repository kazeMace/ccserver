"""
channels/adapters — 各平台 Channel 适配器实现。

每个适配器继承 BaseChannelAdapter，实现平台特定的：
  - start() / stop():  连接/断开外部平台
  - send_text() / send_media(): 发送消息
  - 消息接收:  WebSocket Stream Mode / Webhook / Long Polling 等

已实现的适配器
─────────────
  webchat  — ccserver 内置 WebChat（SSE / WebSocket）
  feishu   — 飞书（参考 openclaw feishu-plus / @larksuiteoapi/feishu-openclaw-plugin）
  dingtalk — 钉钉 Stream Mode（参考 openclaw-dingtalk）
  qqbot    — QQ 机器人（参考 tencent-connect/openclaw-qqbot）

贡献新适配器
────────────
  1. 继承 BaseChannelAdapter
  2. 定义 channel_id、aliases、meta 类属性
  3. 实现 start()、stop()、send_text() 抽象方法
  4. 在 adapters/__init__.py 中导出
  5. 在 server.py 中注册到 ChannelRegistry
"""

from .webchat import WebChatAdapter

__all__ = [
    "WebChatAdapter",
]
