"""
agent.compact_coordinator — 上下文压缩(compaction)的触发与编排。

背景：
  Agent._loop() 每轮开始会检查是否需要压缩历史消息(防止 token 超限),
  原 _maybe_compact / _do_compact 两个方法直接写在 Agent 内。

设计：
  抽出 CompactCoordinator,作为 self.compactor(由 CompactorFactory 构造)的
  触发/编排层。它只依赖 AgentRuntime 契约 + compactor 实例,不与其它协作者耦合。
  行为与重构前完全一致(含 hook 触发、circuit_breaker、persist 分支)。
"""

from __future__ import annotations

from loguru import logger

from ..compact.tokens import estimate_tokens as _estimate_tokens
from .runtime import AgentRuntime


class CompactCoordinator:
    """
    上下文压缩协调器,被 Agent 持有(组合)。

    依赖:
        rt        AgentRuntime — 提供 context/session/emitter/persist 等
        compactor              — 实际执行压缩的对象(Agent.compactor)
    """

    def __init__(self, rt: AgentRuntime, compactor):
        self._rt = rt
        self._compactor = compactor

    async def maybe_compact(self) -> None:
        """
        每轮开始时调用:先跑 micro 压缩,再判断是否需要完整压缩。

        与原 Agent._maybe_compact 行为一致。
        """
        rt = self._rt
        from ccserver.messages import UnifiedMessage, unified_message_to_wire, wire_to_unified_message
        # compact 内部是 dict 工具：转 wire dict 处理，处理后从结果重建 context.messages
        wire = [unified_message_to_wire(m) for m in rt.context.messages]
        self._compactor.run_micro(wire, rt._last_assistant_time)
        rt.context.messages[:] = [wire_to_unified_message(m) for m in wire]
        should, reason = self._compactor.should_compact(wire)
        if should:
            logger.info("Compact triggered | agent={} reason={}", rt.aid_label, reason)
            await self.do_compact(reason=reason)

    async def do_compact(self, reason: str) -> None:
        """
        执行一次完整压缩:触发 hook、调用 compactor、回写消息、记录熔断器状态。

        与原 Agent._do_compact 行为一致。
        """
        rt = self._rt
        from ccserver.messages import UnifiedMessage, unified_message_to_wire, wire_to_unified_message
        # compactor 是 dict 工具：在边界转 wire dict
        wire = [unified_message_to_wire(m) for m in rt.context.messages]
        message_count = len(wire)
        tokens_before = _estimate_tokens(wire)
        # hook: agent:compact:before（observing）
        await rt.session.hooks.emit_void(
            "agent:compact:before",
            {"message_count": message_count, "token_count": tokens_before, "reason": reason},
            rt._build_hook_ctx(),
        )
        await rt.emitter.emit_compact(reason)
        try:
            compacted = await self._compactor.compact(
                wire,
                rt.session,
                rt.emitter,
                lib=rt.prompt_engine,
            )
            if self._compactor.circuit_breaker:
                self._compactor.circuit_breaker.record_success()
        except Exception:
            if self._compactor.circuit_breaker:
                self._compactor.circuit_breaker.record_failure()
            raise
        tokens_after = _estimate_tokens(compacted)
        summary_length = len(compacted[0]["content"]) if compacted else 0
        compacted_count = message_count - len(compacted)
        if rt.persist:
            rt.session.rewrite_messages([wire_to_unified_message(m) for m in compacted])
        else:
            rt.context.messages[:] = [wire_to_unified_message(m) for m in compacted]
        # hook: agent:compact:after（observing）
        await rt.session.hooks.emit_void(
            "agent:compact:after",
            {
                "compacted_count": compacted_count,
                "summary_length": summary_length,
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
                "reason": reason,
            },
            rt._build_hook_ctx(),
        )
