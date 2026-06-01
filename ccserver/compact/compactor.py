"""
compact/compactor.py — 三层压缩组件的协调器 + 工厂。

Compactor 本身不包含业务逻辑，只负责协调 micro / trigger / full 三个组件：
  - run_micro()      → 调用 MicroCompactor，每轮轻量截断
  - should_compact() → 调用 TriggerPolicy，判断是否触发 full compact
                       + CircuitBreaker 熔断保护
  - compact()        → 调用 FullCompactor，LLM 摘要压缩

CompactorFactory.build_default() 用默认参数一键构建完整 Compactor。
外部代码也可传入自定义组件：
    micro   = MyMicroCompactor()
    trigger = MyTriggerPolicy()
    full    = MyFullCompactor()
    compactor = Compactor(micro=micro, full=full, trigger=trigger)
"""

from loguru import logger

from ..config import KEEP_RECENT, MODEL, THRESHOLD
from ..model import ModelAdapter
from .full import DefaultFullCompactor, FullCompactor
from .micro import DefaultMicroCompactor, MicroCompactor
from .trigger import CircuitBreaker, DefaultTriggerPolicy, TriggerPolicy


class Compactor:
    """
    三层压缩组件的协调器。

    不包含业务逻辑，只负责将三个独立组件串联起来，
    并提供统一的接口给 Agent 调用。

    Args:
        micro:           MicroCompactor 实例，负责轻量截断。
        full:            FullCompactor 实例，负责 LLM 摘要压缩。
        trigger:         TriggerPolicy 实例，负责判断是否触发 full compact。
        circuit_breaker: CircuitBreaker 实例（可选），负责熔断保护。
    """

    def __init__(
        self,
        micro: MicroCompactor,
        full: FullCompactor,
        trigger: TriggerPolicy,
        circuit_breaker: CircuitBreaker | None = None,
    ):
        assert isinstance(micro, MicroCompactor), (
            f"micro 必须实现 MicroCompactor Protocol，got {type(micro)}"
        )
        assert isinstance(full, FullCompactor), (
            f"full 必须实现 FullCompactor Protocol，got {type(full)}"
        )
        assert isinstance(trigger, TriggerPolicy), (
            f"trigger 必须实现 TriggerPolicy Protocol，got {type(trigger)}"
        )
        self.micro = micro
        self.full = full
        self.trigger = trigger
        self.circuit_breaker = circuit_breaker

    def run_micro(self, messages: list) -> list:
        """
        执行轻量截断（每轮 agent loop 开始时调用，无 LLM 调用）。

        Args:
            messages: 当前消息列表。

        Returns:
            截断后的消息列表（通常是同一对象，原地修改）。
        """
        return self.micro.compact(messages)

    def should_compact(self, messages: list) -> tuple[bool, str]:
        """
        判断是否应触发 full compact。

        先检查断路器状态：若已熔断，直接返回 False。
        再委托给 TriggerPolicy 判断。

        Args:
            messages: 当前消息列表。

        Returns:
            (True, reason) 需要压缩；(False, "") 不需要。
        """
        # 断路器熔断时跳过 compact
        if self.circuit_breaker and self.circuit_breaker.is_open():
            logger.warning(
                "Compactor: circuit breaker is OPEN, skipping compact"
            )
            return False, "circuit breaker open"

        return self.trigger.should_compact(messages)

    async def compact(
        self,
        messages: list,
        session,
        emitter,
        lib=None,
    ) -> list:
        """
        执行 LLM 摘要压缩。

        Args:
            messages: 待压缩的消息列表。
            session:  Session 实例（归档 + 持久化）。
            emitter:  BaseEmitter 实例（事件推送）。
            lib:      PromptEngine 实例（控制压缩后消息格式）。

        Returns:
            压缩后的消息列表（通常 2 条）。
        """
        return await self.full.compact(messages, session, emitter, lib=lib)


# ─── CompactorFactory ─────────────────────────────────────────────────────────


class CompactorFactory:
    """
    Compactor 工厂，用默认参数构建完整的三层 Compactor。

    常规用法：
        compactor = CompactorFactory.build_default(adapter=adapter, model=model)

    自定义用法（替换某一层）：
        my_trigger = MyTriggerPolicy(threshold=50000)
        compactor = CompactorFactory.build_default(adapter, trigger=my_trigger)
    """

    @staticmethod
    def build_default(
        adapter: ModelAdapter,
        model: str = MODEL,
        *,
        micro: MicroCompactor | None = None,
        full: FullCompactor | None = None,
        trigger: TriggerPolicy | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> Compactor:
        """
        构建使用默认组件的 Compactor。

        所有层都可以通过关键字参数替换为自定义实现：
          micro / full / trigger / circuit_breaker

        Args:
            adapter:         LLM 适配器，传给 DefaultFullCompactor。
            model:           摘要使用的模型 ID，默认 config.MODEL。
            micro:           自定义 MicroCompactor，默认 DefaultMicroCompactor。
            full:            自定义 FullCompactor，默认 DefaultFullCompactor。
            trigger:         自定义 TriggerPolicy，默认 DefaultTriggerPolicy。
            circuit_breaker: 自定义 CircuitBreaker，默认 CircuitBreaker()。

        Returns:
            配置好的 Compactor 实例。
        """
        resolved_micro   = micro   or DefaultMicroCompactor(keep_recent=KEEP_RECENT)
        resolved_full    = full    or DefaultFullCompactor(adapter=adapter, model=model)
        resolved_trigger = trigger or DefaultTriggerPolicy(threshold=THRESHOLD)
        resolved_breaker = circuit_breaker if circuit_breaker is not None else CircuitBreaker()

        logger.debug(
            "CompactorFactory.build_default | micro={} full={} trigger={}",
            type(resolved_micro).__name__,
            type(resolved_full).__name__,
            type(resolved_trigger).__name__,
        )

        return Compactor(
            micro=resolved_micro,
            full=resolved_full,
            trigger=resolved_trigger,
            circuit_breaker=resolved_breaker,
        )
