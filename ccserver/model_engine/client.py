# ccserver/model_engine/client.py
"""
aimodels.client — Layer 1 健壮 LLM 客户端。

在 ModelAdapter 之上叠加：重试退避、流式解析、响应助手。
零 agent 依赖——只吃 adapter + 数据 + 回调，不碰 session / hooks / emitter / event_bus。

形态二合一：
  - 有状态：构造时 bind 默认 model/system/max_tokens，调用只传 messages。
  - 无状态：构造不绑，每次调用全参数传入。
  调用时显式传入的参数覆盖构造时绑定的默认值。

重试：只认 TransientLLMError（由各 adapter 归一抛出），其余异常立即上抛。
"""

from __future__ import annotations

import asyncio

from loguru import logger

from .errors import TransientLLMError

# 重试配置（最多 3 次，退避 2/5/10 秒）
_MAX_RETRIES = 3
_RETRY_DELAYS = [2, 5, 10]


class LLMCaller:
    """Layer 1 健壮 LLM 客户端。详见模块 docstring。"""

    def __init__(self, adapter, *, model=None, system=None, max_tokens=8000):
        """
        初始化 LLMCaller，绑定 adapter 和默认调用参数。

        Args:
            adapter:    ModelAdapter 实例（实际调用 LLM）。
            model:      默认模型名（可被调用时覆盖）。
            system:     默认 system 块（可被调用时覆盖）。
            max_tokens: 默认 max_tokens（可被调用时覆盖），默认 8000。
        """
        assert adapter is not None, "LLMCaller 需要一个 adapter"
        self._adapter = adapter  # LLMProvider（或旧版 LLMAdapter，向后兼容）
        self._model = model
        self._system = system
        self._max_tokens = max_tokens

    def bind(self, *, model=None, system=None, max_tokens=None) -> "LLMCaller":
        """
        部分应用：返回填好更多默认值的新副本（LangChain 风格）。
        未指定的字段沿用当前实例的默认值。

        Args:
            model:      新的默认模型名，None 表示沿用当前值。
            system:     新的默认 system 块，None 表示沿用当前值。
            max_tokens: 新的默认 max_tokens，None 表示沿用当前值。
        Returns:
            新的 LLMCaller 实例，带更新后的默认值。
        """
        return LLMCaller(
            self._adapter,
            model=model if model is not None else self._model,
            system=system if system is not None else self._system,
            max_tokens=max_tokens if max_tokens is not None else self._max_tokens,
        )

    def _resolve(self, model, system, max_tokens):
        """
        把调用时参数与构造默认值合并（调用参数优先）。

        Args:
            model:      调用时传入的 model，None 表示使用默认值。
            system:     调用时传入的 system，None 表示使用默认值。
            max_tokens: 调用时传入的 max_tokens，None 表示使用默认值。
        Returns:
            (eff_model, eff_system, eff_max_tokens) 三元组，均为实际生效值。
        """
        return (
            model if model is not None else self._model,
            system if system is not None else self._system,
            max_tokens if max_tokens is not None else self._max_tokens,
        )

    async def invoke(
        self,
        messages,
        *,
        model=None,
        system=None,
        tools=None,
        max_tokens=None,
        on_retry=None,
        **kwargs,
    ):
        """
        非流式调用，含重试退避；返回完整 Message 对象。

        Args:
            messages:   Anthropic block 格式消息列表。
            model:      覆盖构造时绑定的默认模型名。
            system:     覆盖构造时绑定的默认 system 块。
            tools:      工具 schema 列表（None 则不传）。
            max_tokens: 覆盖构造时绑定的默认 max_tokens。
            on_retry:   可选 async 回调 on_retry(attempt, error)，每次重试前调用。
            **kwargs:   透传给 adapter.create（如 thinking 等额外参数）。
        Returns:
            adapter.create 的返回值（UnifiedResponse 或旧版 Message，取决于 adapter）。
        Raises:
            TransientLLMError: 重试次数耗尽后仍然失败时上抛。
            其它异常: 不可重试，立即上抛，不消耗重试次数。
        """
        # 合并调用时参数与构造默认值
        eff_model, eff_system, eff_max = self._resolve(model, system, max_tokens)

        for attempt in range(_MAX_RETRIES):
            try:
                # 调用底层 adapter，将所有参数传入
                return await self._adapter.create(
                    model=eff_model,
                    messages=messages,
                    max_tokens=eff_max,
                    system=eff_system,
                    tools=tools,
                    **kwargs,
                )
            except TransientLLMError as e:
                # 瞬态错误：未到上限则退避重试，否则上抛
                if attempt < _MAX_RETRIES - 1:
                    # 在 sleep 之前调用 on_retry 回调（如果提供了）
                    if on_retry is not None:
                        await on_retry(attempt, e)
                    delay = _RETRY_DELAYS[attempt]
                    logger.warning(
                        "LLM transient error, retrying ({}/{}) delay={}s error={}",
                        attempt + 1,
                        _MAX_RETRIES,
                        delay,
                        e,
                    )
                    await asyncio.sleep(delay)
                else:
                    # 已到最大重试次数，上抛异常
                    logger.error(
                        "LLM transient error after {} retries: {}", _MAX_RETRIES, e
                    )
                    raise

        # 不可达（最后一次 attempt 必然 return 或 raise）
        raise AssertionError(
            "unreachable: invoke retry loop exited without return/raise"
        )

    async def invoke_text(self, messages, **kwargs) -> str | None:
        """
        invoke + extract_text 的便捷封装，一步拿到正文字符串。

        跳过 ThinkingBlock，返回第一个 TextBlock 的 text。
        无 TextBlock 时返回 None。

        Args:
            messages: 消息列表，透传给 invoke。
            **kwargs: 其余参数透传给 invoke。
        Returns:
            第一个 TextBlock 的 text 字符串，或 None。
        Raises:
            同 invoke。
        """
        response = await self.invoke(messages, **kwargs)
        return self.extract_text(response)

    async def stream(self, messages, *, on_text=None, on_thinking=None,
                     model=None, system=None, tools=None, max_tokens=None,
                     on_retry=None, **kwargs):
        """
        流式调用：逐 token 通过回调交出，返回含 tool_use 的完整 Message。

        重试边界（关键）：
          只在"首个 token 之前"才重试；一旦吐过任意 token（on_text 或 on_thinking），
          再失败直接上抛，避免重复吐已发送的 token。

        Args:
            messages:     Anthropic block 格式消息列表。
            on_text:      async 回调 on_text(text)，接 text_delta。
            on_thinking:  async 回调 on_thinking(text)，接 thinking_delta。
            model/system/max_tokens/tools/on_retry/**kwargs: 同 invoke。
        Returns:
            完整 UnifiedResponse（或旧版 Message，含 tool_use 块）。
        Raises:
            TransientLLMError: 重试次数耗尽后仍然失败时上抛；
                               或已吐 token 后失败时立即上抛。
            其它异常: 不可重试，立即上抛。
        """
        eff_model, eff_system, eff_max = self._resolve(model, system, max_tokens)

        for attempt in range(_MAX_RETRIES):
            emitted = False  # 本次尝试是否已吐过 token
            try:
                # async with 必须在重试循环内层：每次重试重建 stream
                async with self._adapter.stream(
                    model=eff_model,
                    messages=messages,
                    max_tokens=eff_max,
                    system=eff_system,
                    tools=tools,
                    **kwargs,
                ) as stream_ctx:
                    async for delta in stream_ctx:
                        if delta.kind == "text":
                            emitted = True
                            if on_text is not None:
                                await on_text(delta.text)
                        elif delta.kind == "thinking":
                            emitted = True
                            if on_thinking is not None:
                                await on_thinking(delta.text)
                    return await stream_ctx.get_final_message()
            except TransientLLMError as e:
                # 已吐 token：不可重试（避免重复 token），直接上抛
                if emitted:
                    logger.error("LLM stream failed after first token, not retrying: {}", e)
                    raise
                # 首 token 前：未到上限则退避重试
                if attempt < _MAX_RETRIES - 1:
                    if on_retry is not None:
                        await on_retry(attempt, e)
                    delay = _RETRY_DELAYS[attempt]
                    logger.warning(
                        "LLM stream transient error, retrying ({}/{}) delay={}s error={}",
                        attempt + 1, _MAX_RETRIES, delay, e,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "LLM stream transient error after {} retries: {}", _MAX_RETRIES, e
                    )
                    raise

        # 不可达（最后一次 attempt 必然 return 或 raise）
        raise AssertionError("unreachable: stream retry loop exited without return/raise")

    @staticmethod
    def extract_text(response) -> str | None:
        """
        从响应对象中提取正文文本，兼容新旧两种响应格式。

        新路径（UnifiedResponse）：response.content 是字符串，直接返回。
        旧路径（旧版 Message）：response.content 是 list[Block]，遍历找第一个 TextBlock。

        Args:
            response: adapter 返回的响应对象（UnifiedResponse 或旧版 Message）。
        Returns:
            文本字符串，或 None（无文本内容时）。
        """
        content = getattr(response, "content", None)
        if content is None:
            return None
        # 新路径：content 是字符串（UnifiedResponse）
        if isinstance(content, str):
            return content if content else None
        # 旧路径：content 是 list[Block]（旧 Message），遍历找 TextBlock
        if isinstance(content, list):
            for block in content:
                # 跳过非 text 类型（如 ThinkingBlock）
                if getattr(block, "type", None) == "text":
                    return getattr(block, "text", "")
        return None
