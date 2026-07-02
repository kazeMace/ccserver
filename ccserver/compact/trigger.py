"""
compact/trigger.py — 压缩触发策略组件（TriggerPolicy）+ 断路器（CircuitBreaker）。

职责：
  TriggerPolicy 决定"是否应该触发 full compact"。
  CircuitBreaker 在连续失败时熔断，防止无限循环触发 compact。

可扩展性：
  TriggerPolicy Protocol 定义接口，DefaultTriggerPolicy 是默认实现。
  自定义实现只需实现 should_compact(messages) -> (bool, str)。
"""

from typing import Protocol, runtime_checkable

from loguru import logger

from .tokens import estimate_tokens


# ─── TriggerPolicy Protocol ───────────────────────────────────────────────────


@runtime_checkable
class TriggerPolicy(Protocol):
    """
    压缩触发策略协议。

    should_compact() 在每轮 agent loop 中被调用，决定是否触发 full compact。

    方法：
        should_compact(messages) -> (bool, str)
            返回 (是否触发, 触发原因字符串)。
            未触发时 reason 可为空字符串。
    """

    def should_compact(self, messages: list) -> tuple[bool, str]:
        ...


# ─── DefaultTriggerPolicy ─────────────────────────────────────────────────────


class DefaultTriggerPolicy:
    """
    默认触发策略：两个条件，任一满足即触发。

    条件 1：消息条数超过 max_messages（默认 300）
      → 防止大量短消息无限堆积内存。
      → 先判断，O(1)，比 token 估算快。

    条件 2：估算 token 数超过 threshold（默认 120000 字符 / 4 ≈ 30000 tokens）
      → 接近模型上下文窗口边界时主动压缩。

    Args:
        threshold:    token 阈值（字符数 / 4），超过即触发。
        max_messages: 消息条数上限，超过即触发。
    """

    def __init__(self, threshold: int = None, max_messages: int = 300):
        # 默认从进程级配置取 compaction.threshold
        if threshold is None:
            from ..configuration import get_process_config
            threshold = get_process_config().compaction.threshold
        assert threshold > 0,    f"threshold 必须大于 0，收到: {threshold}"
        assert max_messages > 0, f"max_messages 必须大于 0，收到: {max_messages}"
        self.threshold    = threshold
        self.max_messages = max_messages

    def should_compact(self, messages: list) -> tuple[bool, str]:
        """
        判断是否应触发 full compact。

        Args:
            messages: 当前消息列表。

        Returns:
            (True, reason) 表示需要压缩；(False, "") 表示不需要。
        """
        # 条件 1：消息条数（快路径，O(1)）
        msg_count = len(messages)
        if msg_count > self.max_messages:
            logger.info(
                "TriggerPolicy: message count {} exceeds limit {}", msg_count, self.max_messages
            )
            return True, "message count limit reached"

        # 条件 2：token 估算
        tokens = estimate_tokens(messages)
        if tokens > self.threshold:
            logger.info(
                "TriggerPolicy: tokens {} exceeds threshold {}", tokens, self.threshold
            )
            return True, "token threshold reached"

        logger.debug(
            "TriggerPolicy: no compact needed | msgs={} tokens={} threshold={}",
            msg_count, tokens, self.threshold,
        )
        return False, ""


# ─── CircuitBreaker ───────────────────────────────────────────────────────────


class CircuitBreaker:
    """
    断路器：连续失败超过阈值后熔断，停止触发 compact，防止无限失败循环。

    状态机：
      CLOSED（正常）→ [连续失败 MAX_FAILURES 次] → OPEN（熔断）
      OPEN → [调用 record_success()] → CLOSED

    Args:
        max_failures: 连续失败阈值，默认 3 次。
    """

    MAX_FAILURES = 3

    def __init__(self, max_failures: int = MAX_FAILURES):
        assert max_failures > 0, f"max_failures 必须大于 0，收到: {max_failures}"
        self._max_failures = max_failures
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        """记录一次 compact 失败。"""
        self._consecutive_failures += 1
        logger.warning(
            "CircuitBreaker: compact failure {}/{}",
            self._consecutive_failures, self._max_failures,
        )
        if self.is_open():
            logger.error(
                "CircuitBreaker: OPEN after {} consecutive failures, compact suspended",
                self._consecutive_failures,
            )

    def record_success(self) -> None:
        """记录一次 compact 成功，重置失败计数。"""
        if self._consecutive_failures > 0:
            logger.info(
                "CircuitBreaker: reset after success (was {} failures)",
                self._consecutive_failures,
            )
        self._consecutive_failures = 0

    def is_open(self) -> bool:
        """返回 True 表示熔断中，应跳过 compact。"""
        return self._consecutive_failures >= self._max_failures
