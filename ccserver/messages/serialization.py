"""
ccserver/messages/serialization.py

序列化/反序列化工具函数：
    - block_from_dict：单个 block dict → 类型化 UnifiedBlock
    - unified_message_to_wire：UnifiedMessage → wire dict（storage/recorder 边界使用）
    - wire_to_unified_message：wire dict → UnifiedMessage（从 storage 加载时使用）

Serialization / deserialization utilities:
    - block_from_dict: single block dict → typed UnifiedBlock
    - unified_message_to_wire: UnifiedMessage → wire dict (storage/recorder boundary)
    - wire_to_unified_message: wire dict → UnifiedMessage (loading from storage)

零外部依赖：只依赖本包的 blocks 和 unified_message 模块。
Zero external deps: only depends on this package's blocks and unified_message modules.
"""

from __future__ import annotations

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
from .unified_message import UnifiedMessage


# ── block 类型分派表 ───────────────────────────────────────────────────────────
# key 为 "type" 字段值，value 为对应 from_dict 工厂函数。
# Key is the "type" field value; value is the corresponding from_dict factory function.
_BLOCK_FROM_DICT: dict = {
    "text":            UnifiedTextBlock.from_dict,
    "thinking":        UnifiedThinkingBlock.from_dict,
    "tool_use":        UnifiedToolUseBlock.from_dict,
    "tool_result":     UnifiedToolResultBlock.from_dict,
    "image":           UnifiedImageBlock.from_dict,
    "image_thumbnail": UnifiedImageThumbnailBlock.from_dict,
    "file":            UnifiedFileBlock.from_dict,
    "command":         UnifiedCommandBlock.from_dict,
}


def block_from_dict(d: dict) -> UnifiedBlock:
    """
    单个 block dict → 类型化 UnifiedBlock。
    未知 type → UnifiedPassthroughBlock（不丢数据）。
    Single block dict → typed UnifiedBlock.
    Unknown type → UnifiedPassthroughBlock (no data loss).

    分派规则 / Dispatch rules:
        1. 传入非 dict → 原样返回（容错处理）
           Non-dict input → returned as-is (error tolerance)
        2. d.get("_type") == "command" → UnifiedCommandBlock（优先于 type 字段）
           d.get("_type") == "command" → UnifiedCommandBlock (takes priority over "type")
        3. d.get("type") 在分派表中 → 对应子类
           d.get("type") in dispatch table → corresponding subclass
        4. 未知 type → UnifiedPassthroughBlock
           Unknown type → UnifiedPassthroughBlock

    参数 / Args:
        d: 包含块数据的字典 / Dict containing block data

    返回 / Returns:
        UnifiedBlock 子类实例 / UnifiedBlock subclass instance
    """
    # 容错：非 dict 直接返回（防御调用方传错类型）
    # Error tolerance: return non-dict as-is (defensive against caller type errors)
    if not isinstance(d, dict):
        return d

    # CommandBlock 使用 "_type" 而非 "type"，需要优先检查
    # CommandBlock uses "_type" instead of "type", must check first
    if d.get("_type") == "command":
        return UnifiedCommandBlock.from_dict(d)

    # 用 "type" 字段查分派表
    # Look up the dispatch table using the "type" field
    builder = _BLOCK_FROM_DICT.get(d.get("type"))
    if builder is not None:
        return builder(d)

    # 未知类型 → 透传块，不丢失原始数据
    # Unknown type → passthrough block, no data loss
    return UnifiedPassthroughBlock.from_dict(d)


def unified_message_to_wire(msg) -> dict:
    """
    UnifiedMessage → wire dict（storage/recorder 格式）。
    UnifiedMessage → wire dict (storage/recorder format).

    使用场景 / Use cases:
        - storage.append(message) 前的边界转换
          Boundary conversion before storage.append(message)
        - recorder/limit_policy 的 json.dumps 前
          Before recorder/limit_policy's json.dumps
        - hooks/prompt_lib 的 dict-facing 边界
          dict-facing boundary for hooks/prompt_lib

    注意 / Note:
        - provider_data 字段自动剥离（运行时内存，不持久化）
          provider_data is automatically stripped (runtime-only, not persisted)
        - 已是 dict 则原样返回（兼容 S3→S4 过渡期混合场景）
          Already a dict → returned as-is (for S3→S4 migration period mixed scenarios)

    参数 / Args:
        msg: UnifiedMessage 实例，或已是 dict 的消息（过渡期兼容）
             UnifiedMessage instance, or already-a-dict message (migration period compat)

    返回 / Returns:
        dict — wire 格式字典 / wire-format dict
    """
    if isinstance(msg, UnifiedMessage):
        return msg.to_dict()
    # 已是 dict → 原样返回（过渡期兼容，不做任何转换）
    # Already a dict → return as-is (migration period compat, no conversion)
    return msg


def wire_to_unified_message(d: dict) -> UnifiedMessage:
    """
    wire dict → UnifiedMessage（从 storage 加载时使用）。
    wire dict → UnifiedMessage (used when loading from storage).

    向后兼容 / Backward compatibility:
        能读取旧格式磁盘 JSONL：
        Can read old-format disk JSONL:
        - content 为字符串 → 单个 UnifiedTextBlock
          content is string → single UnifiedTextBlock
        - content 为 {"_type":"command",...} → UnifiedCommandBlock
          content is {"_type":"command",...} → UnifiedCommandBlock
        - content 为未知 dict → UnifiedPassthroughBlock
          content is unknown dict → UnifiedPassthroughBlock
        - content 为 list → 逐个分派为类型化 block
          content is list → each dispatched to typed block
        - 非 role/content 的顶层键 → 收进 metadata（passthrough 透传）
          Top-level keys other than role/content → collected into metadata (passthrough)

    参数 / Args:
        d: 来自 storage 的 wire dict / Wire dict from storage

    返回 / Returns:
        UnifiedMessage 实例 / UnifiedMessage instance
    """
    # 断言输入类型，防御调用方传错
    # Assert input type, defensive against caller errors
    assert isinstance(d, dict), f"wire_to_unified_message 期望 dict，收到 {type(d)}"

    role = d.get("role", "user")
    content = d.get("content", "")

    if isinstance(content, str):
        # 旧格式兼容：字符串 content → 单个 TextBlock
        # Old format compat: string content → single TextBlock
        blocks = [UnifiedTextBlock(text=content)]

    elif isinstance(content, dict):
        # dict content：可能是 CommandBlock 或未知格式
        # dict content: may be CommandBlock or unknown format
        if content.get("_type") == "command":
            blocks = [UnifiedCommandBlock.from_dict(content)]
        else:
            # 未知 dict → PassthroughBlock，保留原始数据
            # Unknown dict → PassthroughBlock, preserve raw data
            blocks = [UnifiedPassthroughBlock(
                type=str(content.get("_type") or content.get("type") or "unknown"),
                raw=content,
            )]

    elif isinstance(content, list):
        # list content → 逐个分派为类型化 block
        # list content → each dispatched to typed block
        blocks = [block_from_dict(b) for b in content]

    else:
        # 其他类型（None 等）→ 空列表
        # Other types (None, etc.) → empty list
        blocks = []

    # 非 role/content 的顶层键收进 metadata（保持 passthrough 透传）
    # Collect non-role/content top-level keys into metadata (passthrough preserved)
    metadata = {k: v for k, v in d.items() if k not in ("role", "content")}

    return UnifiedMessage(role=role, content=blocks, metadata=metadata)
