from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

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
    if not isinstance(attr, str):
        raise TypeError(f"attr 必须是字符串，收到: {type(attr)}")

    if isinstance(block, dict):
        return block.get(attr)
    return getattr(block, attr, None)


def estimate_tokens(messages: list[Any]) -> int:
    """对消息列表做一个廉价的 token 数量估算。

    该方法基于字符长度除以 4 进行估算，精度较低但计算开销极小。
    适用于快速判断上下文是否过长、是否需要触发压缩等场景。
    如需精确值，应使用 tiktoken 等 tokenizer 库。

    注意事项：
    - image / document block 固定计 2000 tokens，避免 base64 字符串虚高估算
      （base64 图片达几十万字符，按 len/4 会高估 250 倍，导致频繁触发 compact）
    - thinking block 的 thinking 字段被排除在外，只计 type + signature
      （thinking 内容在多轮对话中只需回传 signature，不占实际发送 token）
    - tool_result 嵌套的 image/document 同样按 2000 tokens 计

    Args:
        messages: 消息列表，通常为 role/content 格式的字典列表。

    Returns:
        估算的 token 数量（整数），不会为负数。

    Raises:
        TypeError: 如果 messages 不是列表类型。
    """
    # 委托给 compact.tokens 的实现，保持逻辑统一
    from ccserver.compact.tokens import estimate_tokens as _estimate
    return _estimate(messages)


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
