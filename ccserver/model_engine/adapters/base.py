"""ProtocolAdapter ABC — HTTP 层，持有 SDK client，只负责发请求，不做格式转换。

设计原则：
- SRP（单一职责）：只负责与 SDK 通信，不做消息格式转换（格式转换由 Codec 负责）
- DIP（依赖倒置）：上层依赖此抽象，不依赖具体 SDK 实现
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any


class ProtocolAdapter(ABC):
    """
    协议适配器根基类。

    持有 SDK client，发请求返回 raw native response。
    不做任何格式转换（格式转换由 Codec 负责）。

    子类必须实现：
    - call()：非流式调用，返回 SDK 原始响应对象
    - stream()：流式调用，返回 SDK 原生 stream context manager
    """

    @abstractmethod
    async def call(self, **native_params: Any) -> Any:
        """
        非流式调用。

        Args:
            **native_params: 由 Codec.encode 产出的 dict，直接 **解包 传入 SDK。

        Returns:
            SDK 原始响应对象（不转换）。例如 Anthropic SDK 的 Message 对象。
        """

    @abstractmethod
    def stream(self, **native_params: Any) -> Any:
        """
        流式调用。

        Args:
            **native_params: 由 Codec.encode 产出的 dict。

        Returns:
            SDK 原生 stream context manager，由 ProviderStream 包装迭代。
        """
