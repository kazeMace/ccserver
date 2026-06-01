"""
compact/strip.py — 压缩前图片/文档剥离工具。

设计背景（参考 CC compact.ts stripImagesFromMessages）：
  压缩请求本身因含大量图片可能触发 prompt-too-long 错误，
  因此必须在发 LLM 摘要请求之前先剥离所有图片和文档。

处理规则：
  - 只处理 user 消息（assistant 消息不含图片）
  - 两层剥离：
      1. 直接位于 content 列表的 image/document block
      2. tool_result 嵌套的 image/document block
  - 替换策略：不是删除，而是替换为 [image] / [document] 文本占位，
    使 LLM 仍能感知"这里曾有图片/文档"，保证摘要语义不丢失
  - 不修改原列表，返回新列表
"""

from loguru import logger


def strip_images_from_messages(messages: list) -> list:
    """
    剥离消息列表中的所有图片和文档 block，替换为文本占位符。

    Args:
        messages: 原始消息列表（Anthropic role/content 格式），不会被修改。

    Returns:
        新消息列表，图片/文档已替换为 [image] / [document]。
    """
    result = []
    stripped_count = 0

    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            # 非 user 消息不处理，直接保留
            result.append(msg)
            continue

        content = msg.get("content")
        if not isinstance(content, list):
            result.append(msg)
            continue

        new_content, msg_stripped = _strip_content_blocks(content)

        if msg_stripped:
            stripped_count += msg_stripped
            # 返回新消息对象，不修改原对象
            result.append({**msg, "content": new_content})
        else:
            result.append(msg)

    if stripped_count:
        logger.debug("strip_images: replaced {} image/document blocks with placeholders", stripped_count)

    return result


def _strip_content_blocks(content: list) -> tuple[list, int]:
    """
    处理单条消息的 content 列表，剥离图片/文档。

    Args:
        content: 消息的 content 列表。

    Returns:
        (new_content, stripped_count) — 新内容列表和剥离的 block 数量。
    """
    new_content = []
    stripped = 0

    for block in content:
        if not isinstance(block, dict):
            new_content.append(block)
            continue

        block_type = block.get("type", "")

        if block_type == "image":
            # 直接图片 block → 文本占位
            new_content.append({"type": "text", "text": "[image]"})
            stripped += 1

        elif block_type == "document":
            # 直接文档 block → 文本占位
            new_content.append({"type": "text", "text": "[document]"})
            stripped += 1

        elif block_type == "tool_result":
            # tool_result 内容可能嵌套图片/文档
            inner = block.get("content")
            if isinstance(inner, list):
                new_inner, inner_stripped = _strip_tool_result_content(inner)
                if inner_stripped:
                    stripped += inner_stripped
                    new_content.append({**block, "content": new_inner})
                else:
                    new_content.append(block)
            else:
                new_content.append(block)

        else:
            new_content.append(block)

    return new_content, stripped


def _strip_tool_result_content(inner: list) -> tuple[list, int]:
    """
    处理 tool_result 嵌套的 content 列表，剥离图片/文档。

    Args:
        inner: tool_result.content 列表。

    Returns:
        (new_inner, stripped_count)。
    """
    new_inner = []
    stripped = 0

    for item in inner:
        if not isinstance(item, dict):
            new_inner.append(item)
            continue

        item_type = item.get("type", "")

        if item_type == "image":
            new_inner.append({"type": "text", "text": "[image]"})
            stripped += 1

        elif item_type == "document":
            new_inner.append({"type": "text", "text": "[document]"})
            stripped += 1

        else:
            new_inner.append(item)

    return new_inner, stripped
