# ccserver/messages/__init__.py
"""messages — 统一类型包（零依赖）。"""

from .blocks import (
    UnifiedBlock,
    UnifiedTextBlock,
    UnifiedThinkingBlock,
    UnifiedToolUseBlock,
    UnifiedToolResultBlock,
    UnifiedImageBlock,
    UnifiedImageThumbnailBlock,
    UnifiedFileBlock,
    UnifiedCommandBlock,
    UnifiedPassthroughBlock,
)
from .usage import UnifiedUsage
from .thinking import ThinkingConfig
from .tool_call import UnifiedToolCall
from .unified_message import UnifiedMessage
from .unified_response import UnifiedResponse
from .stream import UnifiedStreamDelta, StreamState
from .serialization import block_from_dict, unified_message_to_wire, wire_to_unified_message

__all__ = [
    "UnifiedBlock",
    "UnifiedTextBlock", "UnifiedThinkingBlock", "UnifiedToolUseBlock",
    "UnifiedToolResultBlock", "UnifiedImageBlock", "UnifiedImageThumbnailBlock",
    "UnifiedFileBlock", "UnifiedCommandBlock", "UnifiedPassthroughBlock",
    "UnifiedUsage", "ThinkingConfig", "UnifiedToolCall",
    "UnifiedMessage", "UnifiedResponse",
    "UnifiedStreamDelta", "StreamState",
    "block_from_dict", "unified_message_to_wire", "wire_to_unified_message",
]
