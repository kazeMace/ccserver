"""ccserver/model_engine/providers/base.py

LLMProvider ABC + BaseLLMProvider — Provider 层根基类。

LLMProvider：持有 Adapter + Codec，实现 create/stream 对外接口。
BaseLLMProvider：通用实现，encode → adapter.call → decode，子类无需 override。

设计说明：
  - SRP（单一职责）：Provider 只负责"组合 Adapter + Codec，协调调用流程"
  - DIP（依赖倒置）：只依赖 ProtocolAdapter/ProtocolCodec 抽象，不依赖具体实现
  - TYPE_CHECKING 用于类型注解 import，防止循环引用（运行时不 import）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from ccserver.messages import UnifiedMessage, UnifiedResponse, ThinkingConfig
from ccserver.model_engine.metadata.compatibility import ModelCompatibility

# TYPE_CHECKING 块：仅用于类型注解，运行时不 import，防止循环引用
# TYPE_CHECKING block: only for type hints, not imported at runtime (avoids circular imports)
if TYPE_CHECKING:
    from ccserver.model_engine.metadata.model_info import ModelInfo
    from ccserver.model_engine.adapters.base import ProtocolAdapter
    from ccserver.model_engine.codecs.base import ProtocolCodec
    from .stream import ProviderStream


class LLMProvider(ABC):
    """
    LLM Provider 根基类。

    持有 Adapter + Codec，暴露 create / stream 接口。
    model_info 和 compatibility 由外部（如 AdapterFactory）注入。

    设计要点：
      - model_info: ModelInfo | None — 模型固有能力（支持图像、工具等）
      - compatibility: ModelCompatibility — endpoint 协议兼容性（默认全部开启）
      - create()：非流式调用，返回 UnifiedResponse
      - stream()：流式调用，返回 ProviderStream（async context manager + async iterator）

    LLM Provider base class.
    Holds Adapter + Codec, exposes create / stream interfaces.
    model_info and compatibility are injected externally (e.g. by AdapterFactory).
    """

    # 模型固有能力信息（可由 AdapterFactory 注入）
    # Model intrinsic capability info (injected by AdapterFactory)
    model_info: "ModelInfo | None" = None

    # endpoint 协议兼容性（默认值覆盖大多数场景）
    # Endpoint protocol compatibility (defaults cover most scenarios)
    compatibility: ModelCompatibility = ModelCompatibility()

    # ── 能力查询属性（转发到 model_info / compatibility）───────────────────────

    @property
    def supports_image(self) -> bool:
        """模型是否支持图像输入。依赖 model_info，未注入时返回 False。"""
        if self.model_info is None:
            return False
        return self.model_info.supports_image

    @property
    def supports_image_in_tool_result(self) -> bool:
        """tool_result 中是否可以包含图像 block。"""
        return self.compatibility.supports_image_in_tool_result

    @property
    def supports_tools(self) -> bool:
        """是否支持 function calling（tools 参数）。"""
        return self.compatibility.supports_tools

    # ── 抽象接口（子类必须实现）──────────────────────────────────────────────

    @abstractmethod
    async def create(
        self,
        *,
        model: str,
        messages: list[UnifiedMessage],
        max_tokens: int,
        system: "str | None" = None,
        tools: "list[dict] | None" = None,
        thinking: "ThinkingConfig | None" = None,
        **kwargs: Any,
    ) -> UnifiedResponse:
        """
        非流式调用，返回完整 UnifiedResponse。

        Args:
            model:      模型 ID，如 "claude-sonnet-4-6"
            messages:   统一消息列表
            max_tokens: 最大输出 token 数
            system:     系统提示（可选）
            tools:      工具定义列表（可选）
            thinking:   思考链配置（可选）
            **kwargs:   provider 专属额外参数（透传）

        Returns:
            UnifiedResponse — 统一响应对象
        """

    @abstractmethod
    def stream(
        self,
        *,
        model: str,
        messages: list[UnifiedMessage],
        max_tokens: int,
        system: "str | None" = None,
        tools: "list[dict] | None" = None,
        thinking: "ThinkingConfig | None" = None,
        **kwargs: Any,
    ) -> "ProviderStream":
        """
        流式调用，返回 ProviderStream。

        使用方式：
            async with provider.stream(...) as ps:
                async for delta in ps:
                    print(delta.text)
                response = await ps.get_final_response()

        Args:
            model:      模型 ID
            messages:   统一消息列表
            max_tokens: 最大输出 token 数
            system:     系统提示（可选）
            tools:      工具定义列表（可选）
            thinking:   思考链配置（可选）
            **kwargs:   provider 专属额外参数（透传）

        Returns:
            ProviderStream — 流式响应包装器（async context manager + async iterator）
        """


class BaseLLMProvider(LLMProvider):
    """
    通用 Provider 实现：encode → adapter.call → decode。

    子类注入 adapter + codec 即可，通常无需 override create/stream。
    流程：
      1. pre_encode_hook（消息预处理/过滤）
      2. codec.encode_*（messages / tools / thinking → native params）
      3. post_encode_hook（params 后处理）
      4. adapter.call / adapter.stream（发实际请求）
      5. codec.decode_response（非流式）或 ProviderStream（流式）

    Generic Provider implementation: encode → adapter.call → decode.
    Subclasses inject adapter + codec; typically no need to override create/stream.
    """

    def __init__(self, adapter: "ProtocolAdapter", codec: "ProtocolCodec"):
        """
        初始化 BaseLLMProvider。

        Args:
            adapter: ProtocolAdapter 实例，负责与 SDK 通信
            codec:   ProtocolCodec 实例，负责 unified ↔ native 格式转换

        Raises:
            AssertionError: adapter 或 codec 为 None 时抛出
        """
        assert adapter is not None, "BaseLLMProvider: adapter 不能为 None"
        assert codec is not None, "BaseLLMProvider: codec 不能为 None"
        self.adapter = adapter
        self.codec = codec

    async def create(
        self,
        *,
        model: str,
        messages: list[UnifiedMessage],
        max_tokens: int,
        system: "str | None" = None,
        tools: "list[dict] | None" = None,
        thinking: "ThinkingConfig | None" = None,
        **kwargs: Any,
    ) -> UnifiedResponse:
        """
        非流式调用：encode → adapter.call → decode。

        Args:
            model:      模型 ID
            messages:   统一消息列表（list[UnifiedMessage]）
            max_tokens: 最大输出 token 数
            system:     系统提示（可选）
            tools:      工具定义列表（可选）
            thinking:   思考链配置（可选）
            **kwargs:   provider 专属额外参数（透传给 native params）

        Returns:
            UnifiedResponse — 统一响应对象
        """
        # 1. 消息预处理（sanitize、过滤内部 block 等）
        # Pre-process messages (sanitize, filter internal blocks, etc.)
        messages = self.codec.pre_encode_hook(messages)

        # 2. encode：unified → native params
        # Encode: unified messages/tools/thinking → native params dict
        params: dict[str, Any] = {"model": model, "max_tokens": max_tokens}
        params.update(self.codec.encode_messages(messages, system=system))
        params.update(self.codec.encode_tools(tools))

        # thinking 为 None 时跳过，避免子类 encode_thinking 处理 None
        # Skip thinking encoding when None, to avoid subclass encode_thinking receiving None
        if thinking is not None:
            params.update(self.codec.encode_thinking(thinking))

        # 额外 kwargs 透传（如 temperature 等 provider 专属参数）
        # Pass through extra kwargs (e.g. temperature, provider-specific params)
        if kwargs:
            params.update(kwargs)

        # 3. post_encode_hook（追加 provider 特有字段）
        # Post-encode hook: append provider-specific fields
        params = self.codec.post_encode_hook(params)

        # 4. 发请求（瞬态异常由上层 LLMCaller 重试）
        # Send request (transient errors retried by upper LLMCaller)
        native_response = await self.adapter.call(**params)

        # 5. decode：native response → UnifiedResponse
        return self.codec.decode_response(native_response)

    def stream(
        self,
        *,
        model: str,
        messages: list[UnifiedMessage],
        max_tokens: int,
        system: "str | None" = None,
        tools: "list[dict] | None" = None,
        thinking: "ThinkingConfig | None" = None,
        **kwargs: Any,
    ) -> "ProviderStream":
        """
        流式调用：encode → 返回 ProviderStream（包装 adapter.stream）。

        注意：此方法是同步的，返回 ProviderStream 对象。
        调用者须用 async with ... as ps, async for delta in ps 使用。

        Args:
            model:      模型 ID
            messages:   统一消息列表
            max_tokens: 最大输出 token 数
            system:     系统提示（可选）
            tools:      工具定义列表（可选）
            thinking:   思考链配置（可选）
            **kwargs:   provider 专属额外参数

        Returns:
            ProviderStream — 包装了 raw_stream 的流式响应对象
        """
        # 延迟 import，防止循环引用（ProviderStream 和 BaseLLMProvider 互相引用 codec）
        # Deferred import to avoid circular reference
        from .stream import ProviderStream

        # 1. 消息预处理
        messages = self.codec.pre_encode_hook(messages)

        # 2. encode
        params: dict[str, Any] = {"model": model, "max_tokens": max_tokens}
        params.update(self.codec.encode_messages(messages, system=system))
        params.update(self.codec.encode_tools(tools))

        if thinking is not None:
            params.update(self.codec.encode_thinking(thinking))

        if kwargs:
            params.update(kwargs)

        # 3. post_encode_hook
        params = self.codec.post_encode_hook(params)

        # 4. 获取 raw stream（同步，不发请求，只构造 context manager）
        raw_stream = self.adapter.stream(**params)

        # 5. 包装成 ProviderStream 返回
        return ProviderStream(raw_stream=raw_stream, codec=self.codec)
