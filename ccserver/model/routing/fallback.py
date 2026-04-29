"""
fallback — VLM 调用 Fallback 链。

当首选 VLM provider 调用失败时，自动尝试下一个候选。
按 autoPriority 排序依次执行，直到成功或耗尽。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from loguru import logger

from .router import RouteResult


class FallbackChain:
    """
    VLM 调用失败时的 fallback 链。

    按 autoPriority（优先级从高到低）依次尝试每个候选 provider。
    如果某个候选失败（抛出异常），自动切换到下一个。

    Usage:
        candidates = router.get_fallback_candidates()
        chain = FallbackChain(candidates)

        async def call_vlm(route):
            return await describe_image_with_model(img, adapter=route.adapter, model=route.model)

        text, used_route = await chain.execute(call_vlm)
    """

    def __init__(self, candidates: list[RouteResult]):
        """
        初始化 fallback 链。

        Args:
            candidates: 按 priority 排序的候选列表（priority 越低越优先）
        """
        assert candidates, "candidates must not be empty"
        self._candidates = candidates

    async def execute(
        self,
        call_fn: Callable[[RouteResult], Awaitable[Any]],
    ) -> tuple[Any, RouteResult]:
        """
        依次尝试每个候选 provider，返回第一个成功的结果。

        Args:
            call_fn: 异步调用函数，接收 RouteResult，返回任意结果

        Returns:
            (调用结果, 实际使用的 RouteResult)

        Raises:
            RuntimeError: 所有候选都失败
        """
        last_error: Exception | None = None

        for i, candidate in enumerate(self._candidates):
            logger.debug(
                "FallbackChain 尝试 [{}/{}] | provider={} model={} priority={}",
                i + 1, len(self._candidates),
                candidate.provider_id, candidate.model, candidate.priority,
            )

            try:
                result = await call_fn(candidate)
                logger.info(
                    "FallbackChain 成功 [{}/{}] | provider={} model={}",
                    i + 1, len(self._candidates),
                    candidate.provider_id, candidate.model,
                )
                return result, candidate

            except Exception as e:
                logger.warning(
                    "FallbackChain 失败 [{}/{}] | provider={} model={} error={}",
                    i + 1, len(self._candidates),
                    candidate.provider_id, candidate.model, e,
                )
                last_error = e
                continue

        # 所有候选都失败
        provider_list = ", ".join(c.provider_id for c in self._candidates)
        raise RuntimeError(
            f"FallbackChain 所有候选 VLM provider 均失败（共 {len(self._candidates)} 个）。"
            f"已尝试：{provider_list}。最后错误：{last_error}"
        )

    async def execute_with_timeout(
        self,
        call_fn: Callable[[RouteResult], Awaitable[Any]],
        timeout_per_call: float = 30.0,
    ) -> tuple[Any, RouteResult]:
        """
        依次尝试，每次调用有超时限制。

        Args:
            call_fn:           异步调用函数
            timeout_per_call:  单个候选的超时时间（秒）

        Returns:
            (调用结果, 实际使用的 RouteResult)

        Raises:
            RuntimeError: 所有候选都失败或超时
        """
        import asyncio

        async def call_with_timeout(candidate: RouteResult) -> Any:
            """为单次调用添加超时保护。"""
            return await asyncio.wait_for(
                call_fn(candidate),
                timeout=timeout_per_call,
            )

        return await self.execute(call_with_timeout)

    @property
    def candidate_count(self) -> int:
        """候选 provider 数量。"""
        return len(self._candidates)
