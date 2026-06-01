"""
compact/micro.py — 轻量截断组件（MicroCompactor）。

职责：每轮 agent loop 开始时原地截断旧 tool_result 内容，
     无需调用 LLM，是减少 token 占用的第一道防线。

可扩展性：
  MicroCompactor Protocol 定义接口，DefaultMicroCompactor 是默认实现。
  自定义实现只需实现 compact(messages) -> list 即可。
"""

from typing import Protocol, runtime_checkable

from loguru import logger

from ..config import KEEP_RECENT


# ─── MicroCompactor Protocol ──────────────────────────────────────────────────


@runtime_checkable
class MicroCompactor(Protocol):
    """
    轻量截断协议。

    实现此协议可替换默认的 tool_result 截断逻辑。
    compact() 应原地修改或返回新列表，调用方会用返回值更新消息列表。

    方法：
        compact(messages) -> list
            对消息列表做轻量清理，返回（可能修改的）消息列表。
    """

    def compact(self, messages: list) -> list:
        ...


# ─── DefaultMicroCompactor ────────────────────────────────────────────────────


class DefaultMicroCompactor:
    """
    默认轻量截断实现。

    策略：
    - 保留最近 keep_recent 条 tool_result 完整内容
    - 更早的 tool_result：
        * 普通字符串（>100 字符）→ "[Previous: used {tool_name}]"
        * 含 image/document block 的多模态结果 → "[Previous: used {tool_name} — {text}]"
    - 增量跳过：消息数与上次相同则直接返回，避免每轮全量遍历

    Args:
        keep_recent: 保留完整内容的最近 tool_result 数量，默认 KEEP_RECENT。
    """

    def __init__(self, keep_recent: int = KEEP_RECENT):
        assert keep_recent > 0, f"keep_recent 必须大于 0，收到: {keep_recent}"
        self.keep_recent = keep_recent
        # 上次处理时的消息数，用于增量跳过
        self._last_count: int = 0

    def compact(self, messages: list) -> list:
        """
        原地截断旧 tool_result，减少 token 占用。

        Args:
            messages: 当前消息列表（会被原地修改）。

        Returns:
            修改后的消息列表（同一对象）。
        """
        # 增量跳过：消息数没变，上次已经处理过了
        current_count = len(messages)
        if current_count == self._last_count:
            return messages
        self._last_count = current_count

        # 收集所有 tool_result block（按出现顺序）
        tool_results = []
        for msg in messages:
            if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                for part in msg["content"]:
                    if isinstance(part, dict) and part.get("type") == "tool_result":
                        tool_results.append(part)

        # 不超过 keep_recent 条，无需截断
        if len(tool_results) <= self.keep_recent:
            return messages

        # 构建 tool_use_id → tool_name 映射（用于生成占位符标签）
        tool_name_map: dict[str, str] = {}
        for msg in messages:
            if msg.get("role") == "assistant":
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    # 兼容 dict 格式和对象格式
                    btype = block.get("type") or getattr(block, "type", None)
                    bid   = block.get("id")   or getattr(block, "id", None)
                    bname = block.get("name") or getattr(block, "name", None)
                    if btype == "tool_use" and bid and bname:
                        tool_name_map[bid] = bname

        # 截断最旧的（keep_recent 之外的）tool_result
        truncated = 0
        for result in tool_results[:-self.keep_recent]:
            content = result.get("content")
            tid = result.get("tool_use_id", "")
            tool_name = tool_name_map.get(tid, "unknown")

            if isinstance(content, str) and len(content) > 100:
                # 普通字符串：超 100 字符才截断
                result["content"] = f"[Previous: used {tool_name}]"
                truncated += 1

            elif isinstance(content, list):
                has_media = any(
                    isinstance(b, dict) and b.get("type") in ("image", "document")
                    for b in content
                )
                if has_media:
                    # 多模态结果：保留 text block 摘要，丢弃图片/文档
                    text_parts = [
                        b.get("text", "")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    summary = " | ".join(p for p in text_parts if p) or tool_name
                    result["content"] = f"[Previous: used {tool_name} — {summary}]"
                    truncated += 1

        if truncated:
            logger.debug("micro_compact: truncated {} old tool results", truncated)

        return messages
