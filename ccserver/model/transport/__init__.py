"""
transport — 传输协议层。

将传输协议从 Provider 中解耦，多个 provider 可共享同一个 transport 实现。
"""

from .base import TransportProtocol

__all__ = [
    "TransportProtocol",
]
