from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Iterable

"""
sdk.py — Anthropic SDK 辅助工具函数。

本模块提供与 Anthropic SDK 交互时的通用胶水代码，包括：
- 统一从 SDK 对象或字典读取属性
- 将 SDK 返回的复杂对象序列化为普通字典
- 低成本的 token 估算
- 生成带时间戳的唯一消息 ID

设计目标：让业务层不需要关心 Anthropic SDK 内部对象类型的差异。
"""

logger = logging.getLogger(__name__)

__all__ = [
    "get_block_attr",
    "normalize_content_blocks",
    "estimate_tokens",
    "generate_message_id",
]


def get_block_attr(block: dict[str, Any] | Any, attr: str) -> Any:
    """从 Anthropic SDK 对象或普通字典中获取属性值。

    Anthropic SDK 返回的消息块有时是对象（如 TextBlock），有时是字典。
    该函数统一两种情况，避免调用方关心底层类型。

    Args:
        block: 消息块，可以是 dict 或 SDK 对象。
        attr: 要获取的属性名。

    Returns:
        属性值；如果属性不存在则返回 None。

    Raises:
        TypeError: 如果 attr 不是字符串类型。
    """
    assert isinstance(attr, str), f"attr 必须是字符串，收到: {type(attr)}"

    if isinstance(block, dict):
        return block.get(attr)
    return getattr(block, attr, None)


def normalize_content_blocks(content: Iterable[Any]) -> list[dict[str, Any]]:
    """将 Anthropic SDK 返回的内容块列表转换为纯字典列表。

    SDK 返回的 block 可能是对象（如 TextBlock、ToolUseBlock），无法直接 JSON 序列化。
    该函数遍历每个 block，提取关键字段并转换为字典，方便后续存储、传输或日志记录。

    Args:
        content: 可迭代的 block 集合（列表、元组等）。

    Returns:
        由纯字典组成的列表，每个字典包含对应 block 的类型和字段。

    Raises:
        TypeError: 如果 content 不可迭代（将通过 for 循环自然抛出）。
    """
    assert content is not None, "content 参数不能为 None"

    result: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, dict):
            result.append(block)
        elif get_block_attr(block, "type") == "text":
            result.append({
                "type": "text",
                "text": getattr(block, "text", ""),
            })
        elif get_block_attr(block, "type") == "tool_use":
            result.append({
                "type": "tool_use",
                "id": getattr(block, "id", ""),
                "name": getattr(block, "name", ""),
                "input": getattr(block, "input", {}),
            })
        elif get_block_attr(block, "type") == "thinking":
            result.append({
                "type": "thinking",
                "thinking": getattr(block, "thinking", ""),
            })
        else:
            block_type = str(get_block_attr(block, "type") or "unknown")
            result.append({"type": block_type})
            logger.debug("normalize_content_blocks: 遇到未知类型 block: %s", block_type)
    return result


def estimate_tokens(messages: list[Any]) -> int:
    """对消息列表做一个廉价的 token 数量估算。

    该方法基于字符长度除以 4 进行估算，精度较低但计算开销极小。
    适用于快速判断上下文是否过长、是否需要触发压缩等场景。
    如需精确值，应使用 tiktoken 等 tokenizer 库。

    Args:
        messages: 消息列表，通常为 role/content 格式的字典列表。

    Returns:
        估算的 token 数量（整数），不会为负数。

    Raises:
        TypeError: 如果 messages 不是列表类型。
    """
    assert isinstance(messages, list), f"messages 必须是列表，收到: {type(messages)}"
    token_count = len(str(messages)) // 4
    logger.debug("estimate_tokens: %d messages -> ~%d tokens", len(messages), token_count)
    return token_count


def generate_message_id() -> str:
    """生成一个带时间戳的唯一标识字符串。

    格式为：{uuid4}-{yyyyMMddHHmmssSSS}
    时间戳部分共 17 位数字，便于按时间排序和排查问题。

    Returns:
        唯一标识字符串。
    """
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d%H%M%S") + f"{now.microsecond // 1000:03d}"
    message_id = f"{uuid.uuid4()}-{timestamp}"
    logger.debug("generate_message_id: %s", message_id)
    return message_id
