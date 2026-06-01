"""
compact/tokens.py — token 数量估算工具。

设计要点：
- image / document block 固定计 2000 tokens（参考 CC microCompact.ts IMAGE_MAX_TOKEN_SIZE）
  原因：base64 图片字符串长达几十万字符，若按 len/4 估算会严重虚高（高估 250 倍），
       导致每次截图后立即触发 compact。固定 2000 是保守偏高的合理估算值。
- thinking block 只计 type + signature，不计 thinking 字段
  原因：thinking 内容在多轮对话中只需回传 signature，不占实际发送 token。
- 其余 block / 消息按 len(str(...)) / 4 粗估

此函数替代 ccserver/utils/sdk.py 中的 estimate_tokens，
同时保留 sdk.py 中的版本以向后兼容（可后续迁移）。
"""

from typing import Any

from loguru import logger

# 图片 / 文档固定 token 估算值
# 参考：CC microCompact.ts:38 IMAGE_MAX_TOKEN_SIZE = 2000
# API 实际计费：(width_px * height_px) / 750，最大约 5333 tokens，选 2000 为保守值
IMAGE_TOKEN_SIZE = 2000
DOCUMENT_TOKEN_SIZE = 2000


def estimate_tokens(messages: list[Any]) -> int:
    """
    对消息列表做廉价的 token 数量估算。

    Args:
        messages: 消息列表，Anthropic role/content 格式。

    Returns:
        估算的 token 数量（整数，非负）。

    Raises:
        TypeError: messages 不是列表时抛出。
    """
    if not isinstance(messages, list):
        raise TypeError(f"messages 必须是列表，收到: {type(messages)}")

    total_chars = 0

    for m in messages:
        if not isinstance(m, dict):
            total_chars += len(str(m))
            continue

        content = m.get("content")

        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    total_chars += len(str(block))
                    continue

                block_type = block.get("type", "")

                if block_type == "thinking":
                    # thinking block 只有 type + signature 才是实际发送的内容
                    total_chars += len(block.get("type", "")) + len(block.get("signature", ""))

                elif block_type == "image":
                    # 固定 2000 tokens，避免 base64 字符串虚高
                    # 直接加到 total_chars 前先乘以 4（最终会 /4）
                    total_chars += IMAGE_TOKEN_SIZE * 4

                elif block_type == "document":
                    # PDF 等文档 base64 同样避免虚高
                    total_chars += DOCUMENT_TOKEN_SIZE * 4

                elif block_type == "tool_result":
                    # tool_result 可能嵌套 image/document，递归处理内容
                    inner = block.get("content")
                    if isinstance(inner, str):
                        total_chars += len(inner)
                    elif isinstance(inner, list):
                        for item in inner:
                            if isinstance(item, dict):
                                item_type = item.get("type", "")
                                if item_type == "image":
                                    total_chars += IMAGE_TOKEN_SIZE * 4
                                elif item_type == "document":
                                    total_chars += DOCUMENT_TOKEN_SIZE * 4
                                else:
                                    total_chars += len(str(item))
                            else:
                                total_chars += len(str(item))
                    # tool_use_id / is_error 等字段也占一点
                    total_chars += len(str(block.get("tool_use_id", "")))

                else:
                    total_chars += len(str(block))
        else:
            total_chars += len(str(m))

    token_count = total_chars // 4
    logger.debug("estimate_tokens: {} messages -> ~{} tokens", len(messages), token_count)
    return token_count
