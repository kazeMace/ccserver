"""
sdk — Anthropic SDK 相关工具函数。
"""

import uuid


def _block_get(block, attr: str):
    """Get an attribute from either an Anthropic SDK object or a plain dict."""
    if isinstance(block, dict):
        return block.get(attr)
    return getattr(block, attr, None)


def _normalize_content(content) -> list:
    """Convert Anthropic SDK response objects → plain dicts for JSON serialization."""
    result = []
    for block in content:
        if isinstance(block, dict):
            result.append(block)
        elif _block_get(block, "type") == "text":
            result.append({"type": "text", "text": block.text})
        elif _block_get(block, "type") == "tool_use":
            result.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
        else:
            result.append({"type": str(_block_get(block, "type") or "unknown")})
    return result


def estimate_tokens(messages: list) -> int:
    """Cheap token estimate: characters / 4."""
    return len(str(messages)) // 4


def gen_uuid() -> str:
    """生成一个随机 UUID 字符串，尾缀附加当前时间戳，格式为 yyyyMMddHHmmssSSS。"""
    from datetime import datetime
    now = datetime.now()
    ts = now.strftime("%Y%m%d%H%M%S") + f"{now.microsecond // 1000:03d}"
    return f"{uuid.uuid4()}-{ts}"
