"""
compact/micro.py — 轻量截断组件（MicroCompactor）。

职责：每轮 agent loop 开始时原地截断旧 tool_result 内容，
     无需调用 LLM，是减少 token 占用的第一道防线。

两种截断模式：
  1. token/消息数触发（每轮检查）：旧 tool_result 超出 keep_recent 时截断
  2. 时间触发（60 分钟）：距上次 assistant 消息超过阈值时，
     主动清理可压缩工具的 tool_result（此时 prompt cache 已过期，清理无代价）

可扩展性：
  MicroCompactor Protocol 定义接口，DefaultMicroCompactor 是默认实现。
  自定义实现只需实现 compact(messages, last_assistant_time) -> list 即可。
"""

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from loguru import logger


# ─── 可压缩工具集 ──────────────────────────────────────────────────────────────
# 参考 CC microCompact.ts COMPACTABLE_TOOLS。
# 只截断这些工具的 tool_result，避免误删业务关键工具的输出。
# 这些工具的输出通常是文件/命令/搜索结果，重复性高，旧内容价值低。
COMPACTABLE_TOOLS: frozenset[str] = frozenset([
    "Read",
    "Bash",
    "Grep",
    "Glob",
    "WebSearch",
    "WebFetch",
    "Edit",
    "Write",
    "NotebookEdit",
    "NotebookRead",
    "LS",
])

# 时间触发阈值：距上次 assistant 消息超过此时间（秒）则触发时间压缩
# 参考 CC timeBasedMCConfig.ts：60 分钟（prompt cache TTL）
TIME_COMPACT_THRESHOLD_SECS: int = 60 * 60  # 3600 秒


# ─── MicroCompactor Protocol ──────────────────────────────────────────────────


@runtime_checkable
class MicroCompactor(Protocol):
    """
    轻量截断协议。

    实现此协议可替换默认的 tool_result 截断逻辑。
    compact() 应原地修改或返回新列表，调用方会用返回值更新消息列表。

    方法：
        compact(messages, last_assistant_time) -> list
            对消息列表做轻量清理，返回（可能修改的）消息列表。

    Args:
        messages:             当前消息列表。
        last_assistant_time:  上次 assistant 消息的时间（UTC），用于时间触发判断。
                              None 表示未知，跳过时间触发检查。
    """

    def compact(self, messages: list, last_assistant_time: datetime | None = None) -> list:
        ...


# ─── DefaultMicroCompactor ────────────────────────────────────────────────────


class DefaultMicroCompactor:
    """
    默认轻量截断实现，包含两种截断模式：

    模式 1 — token 驱动截断（每轮）：
      - 只截断 COMPACTABLE_TOOLS 工具的旧 tool_result
      - 保留最近 keep_recent 条完整内容
      - 更早的：普通字符串 → "[Previous: used {tool_name}]"
               含 image/document → "[Previous: used {tool_name} — {text}]"
               list 但无媒体且总长 > 400 → "[Previous: used {tool_name}]"
      - 增量跳过：消息数与上次相同则直接跳过此模式

    模式 2 — 时间驱动截断（60 分钟）：
      - 距上次 assistant 消息超过 TIME_COMPACT_THRESHOLD_SECS
      - 清理所有 COMPACTABLE_TOOLS 的 tool_result（不受 keep_recent 限制）
      - 此时 prompt cache 已过期，清理没有缓存代价

    Args:
        keep_recent:               保留完整内容的最近 tool_result 数量，默认 KEEP_RECENT。
        compactable_tools:         可截断的工具集，默认 COMPACTABLE_TOOLS。
        time_threshold_secs:       时间触发阈值（秒），默认 TIME_COMPACT_THRESHOLD_SECS。
    """

    def __init__(
        self,
        keep_recent: int = None,
        compactable_tools: frozenset[str] = COMPACTABLE_TOOLS,
        time_threshold_secs: int = TIME_COMPACT_THRESHOLD_SECS,
    ):
        # 默认从进程级配置取 compaction.keep_recent
        if keep_recent is None:
            from ..configuration import get_process_config
            keep_recent = get_process_config().compaction.keep_recent
        assert keep_recent > 0, f"keep_recent 必须大于 0，收到: {keep_recent}"
        assert time_threshold_secs > 0, f"time_threshold_secs 必须大于 0，收到: {time_threshold_secs}"
        self.keep_recent = keep_recent
        self.compactable_tools = compactable_tools
        self.time_threshold_secs = time_threshold_secs
        # 上次处理时的消息数，用于模式 1 增量跳过
        self._last_count: int = 0

    def compact(self, messages: list, last_assistant_time: datetime | None = None) -> list:
        """
        执行轻量截断（模式 1 + 模式 2）。

        Args:
            messages:            当前消息列表（会被原地修改）。
            last_assistant_time: 上次 assistant 消息的时间（UTC）。

        Returns:
            修改后的消息列表（同一对象）。
        """
        # 模式 2 优先：时间触发时强制清理所有可压缩工具结果
        if self._should_time_compact(last_assistant_time):
            logger.info(
                "micro_compact: time-based compact triggered "
                "(last_assistant={}, threshold={}s)",
                last_assistant_time, self.time_threshold_secs,
            )
            self._time_compact(messages)
            # 时间压缩后重置增量计数，避免模式 1 跳过
            self._last_count = len(messages)
            return messages

        # 模式 1：token 驱动截断（增量跳过）
        current_count = len(messages)
        if current_count == self._last_count:
            return messages
        self._last_count = current_count

        self._token_compact(messages)
        return messages

    # ── 模式 2：时间驱动截断 ──────────────────────────────────────────────────

    def _should_time_compact(self, last_assistant_time: datetime | None) -> bool:
        """判断是否满足时间触发条件。"""
        if last_assistant_time is None:
            return False
        now = datetime.now(timezone.utc)
        elapsed = (now - last_assistant_time).total_seconds()
        return elapsed >= self.time_threshold_secs

    def _time_compact(self, messages: list) -> None:
        """
        时间触发模式：清理所有 COMPACTABLE_TOOLS 的 tool_result，不受 keep_recent 限制。
        此时 prompt cache 已过期（超过 60 分钟），清理没有缓存代价。
        """
        tool_name_map = self._build_tool_name_map(messages)
        truncated = 0

        for msg in messages:
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            for part in msg["content"]:
                if not isinstance(part, dict) or part.get("type") != "tool_result":
                    continue
                tid = part.get("tool_use_id", "")
                tool_name = tool_name_map.get(tid, "")
                # 只处理可压缩工具
                if tool_name not in self.compactable_tools:
                    continue
                if self._truncate_result(part, tool_name):
                    truncated += 1

        if truncated:
            logger.debug(
                "micro_compact (time-based): truncated {} tool results", truncated
            )

    # ── 模式 1：token 驱动截断 ────────────────────────────────────────────────

    def _token_compact(self, messages: list) -> None:
        """
        token 驱动模式：收集所有可压缩工具的 tool_result，
        保留最近 keep_recent 条，截断其余旧结果。
        """
        tool_name_map = self._build_tool_name_map(messages)

        # 只收集可压缩工具的 tool_result
        compactable_results = []
        for msg in messages:
            if msg.get("role") != "user" or not isinstance(msg.get("content"), list):
                continue
            for part in msg["content"]:
                if not isinstance(part, dict) or part.get("type") != "tool_result":
                    continue
                tid = part.get("tool_use_id", "")
                tool_name = tool_name_map.get(tid, "")
                if tool_name in self.compactable_tools:
                    compactable_results.append((part, tool_name))

        # 不超过 keep_recent 条，无需截断
        if len(compactable_results) <= self.keep_recent:
            return

        truncated = 0
        for result, tool_name in compactable_results[:-self.keep_recent]:
            if self._truncate_result(result, tool_name):
                truncated += 1

        if truncated:
            logger.debug(
                "micro_compact (token-based): truncated {} old tool results", truncated
            )

    # ── 公共辅助 ──────────────────────────────────────────────────────────────

    def _build_tool_name_map(self, messages: list) -> dict[str, str]:
        """从 assistant 消息中构建 tool_use_id → tool_name 映射。"""
        tool_name_map: dict[str, str] = {}
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type") or getattr(block, "type", None)
                bid   = block.get("id")   or getattr(block, "id", None)
                bname = block.get("name") or getattr(block, "name", None)
                if btype == "tool_use" and bid and bname:
                    tool_name_map[bid] = bname
        return tool_name_map

    def _truncate_result(self, result: dict, tool_name: str) -> bool:
        """
        截断单条 tool_result 的内容。

        Returns:
            True 表示发生了截断，False 表示内容不需要截断。
        """
        content = result.get("content")

        if isinstance(content, str):
            if len(content) > 100:
                result["content"] = f"[Previous: used {tool_name}]"
                return True

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
                return True
            else:
                # 纯 text list，按总长度决定是否截断
                total_len = sum(len(str(b)) for b in content)
                if total_len > 400:
                    result["content"] = f"[Previous: used {tool_name}]"
                    return True

        return False
