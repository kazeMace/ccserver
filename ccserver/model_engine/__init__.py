"""aimodels — LLM 接入层门面。

对外暴露：
  - 统一类型系统（UnifiedMessage / UnifiedResponse / UnifiedBlock 子类等）
  - 错误类型（TransientLLMError 及工具函数）
  - Provider 层（LLMProvider / BaseLLMProvider / ProviderStream / 具体实现）
  - Adapter 层（ProtocolAdapter / 具体实现）
  - Codec 层（ProtocolCodec / 具体实现）
  - L1 客户端（LLMCaller）
  - Wiring（AdapterFactory / ModelEndpoint）
  - 模型能力元数据（ModelInfo / ModelInfoRegistry）
  - 旧 alias（过渡期，Task 13-14 消费层迁移完成后删除）
"""

# ── 统一类型系统（新）────────────────────────────────────────────────────────
# New unified type system from ccserver.messages
from ccserver.messages import (
    UnifiedBlock,
    UnifiedTextBlock, UnifiedThinkingBlock, UnifiedToolUseBlock,
    UnifiedToolResultBlock, UnifiedImageBlock, UnifiedImageThumbnailBlock,
    UnifiedFileBlock, UnifiedCommandBlock, UnifiedPassthroughBlock,
    UnifiedUsage, ThinkingConfig, UnifiedToolCall,
    UnifiedMessage, UnifiedResponse,
    UnifiedStreamDelta, StreamState,
    block_from_dict, unified_message_to_wire, wire_to_unified_message,
)

# ── 错误类型 ──────────────────────────────────────────────────────────────────
# Error types and utility functions
from .errors import TransientLLMError, is_transient, wrap_transient

# ── Provider 层（新）─────────────────────────────────────────────────────────
# Provider layer: base classes and concrete implementations
from .providers.base import LLMProvider, BaseLLMProvider
from .providers.stream import ProviderStream
from .providers.anthropic import AnthropicProvider, get_default_provider
from .providers.openai_chat import OpenAIChatProvider

# ── Adapter 层（新）─────────────────────────────────────────────────────────
# Adapter layer: raw SDK communication
from .adapters.base import ProtocolAdapter
from .adapters.anthropic_sdk import AnthropicSDKAdapter
from .adapters.chat_completions import ChatCompletionsAdapter

# ── Codec 层（新）────────────────────────────────────────────────────────────
# Codec layer: unified ↔ native format conversion
from .codecs.base import ProtocolCodec
from .codecs.anthropic import AnthropicCodec
from .codecs.chat_completions import ChatCompletionsCodec

# ── L1 客户端 ────────────────────────────────────────────────────────────────
# L1 robust client (retry / streaming)
from .client import LLMCaller

# ── Wiring ───────────────────────────────────────────────────────────────────
# Factory and endpoint for wiring providers
from .wiring.factory import AdapterFactory
from .wiring.endpoint import ModelEndpoint

# ── 模型能力元数据 ────────────────────────────────────────────────────────────
# Model capability metadata registry
from .metadata import ModelInfo, ModelInfoRegistry, get_registry, BUILTIN_MODEL_CATALOG

# ── 旧 core/ alias（旧文件已删，使用新类型作为别名）────────────────────────────────
# Legacy core/ aliases mapped to new equivalents (old files deleted)
# ModelAdapter / LLMAdapter → LLMProvider（新架构的 Provider 抽象层）
# StreamSession → ProviderStream（新架构的流式消费类）
from .providers.base import LLMProvider as ModelAdapter
from .providers.base import BaseLLMProvider as LLMAdapter
from .providers.stream import ProviderStream as StreamSession

# ── 旧名 alias（消费层过渡期使用）────────────────────────────────────────────
# Old-name aliases for consumer-layer transition period
from ccserver.messages import UnifiedResponse as Message
from ccserver.messages import UnifiedTextBlock as TextBlock
from ccserver.messages import UnifiedThinkingBlock as ThinkingBlock
from ccserver.messages import UnifiedToolUseBlock as ToolUseBlock
from ccserver.messages import UnifiedPassthroughBlock as PassthroughBlock
from ccserver.messages import UnifiedUsage as Usage
from ccserver.messages import UnifiedStreamDelta as StreamDelta

__all__ = [
    # ── 新类型 ──
    "UnifiedBlock",
    "UnifiedTextBlock", "UnifiedThinkingBlock", "UnifiedToolUseBlock",
    "UnifiedToolResultBlock", "UnifiedImageBlock", "UnifiedImageThumbnailBlock",
    "UnifiedFileBlock", "UnifiedCommandBlock", "UnifiedPassthroughBlock",
    "UnifiedUsage", "ThinkingConfig", "UnifiedToolCall",
    "UnifiedMessage", "UnifiedResponse", "UnifiedStreamDelta", "StreamState",
    "block_from_dict", "unified_message_to_wire", "wire_to_unified_message",
    # ── 错误 ──
    "TransientLLMError", "is_transient", "wrap_transient",
    # ── 新 Provider 抽象 ──
    "LLMProvider", "BaseLLMProvider", "ProviderStream",
    # ── 新 Adapter/Codec 抽象 ──
    "ProtocolAdapter", "ProtocolCodec",
    # ── 具体 Provider 实现 ──
    "AnthropicProvider", "get_default_provider",
    "OpenAIChatProvider",
    # ── 具体 Adapter 实现 ──
    "AnthropicSDKAdapter", "ChatCompletionsAdapter",
    # ── 具体 Codec 实现 ──
    "AnthropicCodec", "ChatCompletionsCodec",
    # ── L1 客户端 ──
    "LLMCaller",
    # ── Wiring ──
    "AdapterFactory", "ModelEndpoint",
    # ── 元数据 ──
    "ModelInfo", "ModelInfoRegistry", "get_registry", "BUILTIN_MODEL_CATALOG",
    # ── 旧 alias（过渡，待删除）──
    "Message", "TextBlock", "ThinkingBlock", "ToolUseBlock",
    "PassthroughBlock", "Usage", "StreamDelta",
    "ModelAdapter", "LLMAdapter", "StreamSession",
]
