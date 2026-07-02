# tests/test_llm_caller_l1.py
"""tests/test_llm_caller_l1.py — Layer 1 LLMCaller（重试 / 流式 / extract_text）。"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from ccserver.model_engine.errors import TransientLLMError
from ccserver.model_engine.client import LLMCaller
from ccserver.messages import UnifiedStreamDelta


# ─── 辅助 ────────────────────────────────────────────────────────────────────


def _text_block(text):
    """构造一个 type=text 的 block（用 MagicMock 模拟 SDK block）。"""
    b = MagicMock()
    b.type = "text"
    b.text = text
    return b


def _thinking_block(text):
    b = MagicMock()
    b.type = "thinking"
    b.thinking = text
    return b


def _make_response(blocks, stop_reason="end_turn"):
    resp = MagicMock()
    resp.content = blocks
    resp.stop_reason = stop_reason
    return resp


# ─── invoke 成功路径 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invoke_returns_response_and_passes_params():
    adapter = MagicMock()
    resp = _make_response([_text_block("hi")])
    adapter.create = AsyncMock(return_value=resp)

    caller = LLMCaller(adapter, model="m", max_tokens=100)
    result = await caller.invoke([{"role": "user", "content": "x"}])

    assert result is resp
    adapter.create.assert_awaited_once()
    kw = adapter.create.call_args.kwargs
    assert kw["model"] == "m"
    assert kw["max_tokens"] == 100
    assert kw["messages"] == [{"role": "user", "content": "x"}]


@pytest.mark.asyncio
async def test_invoke_call_params_override_bound_defaults():
    """调用时显式传参覆盖构造时绑定的默认值。"""
    adapter = MagicMock()
    adapter.create = AsyncMock(return_value=_make_response([_text_block("h")]))

    caller = LLMCaller(adapter, model="bound", max_tokens=100)
    await caller.invoke([], model="override", max_tokens=222)

    kw = adapter.create.call_args.kwargs
    assert kw["model"] == "override"
    assert kw["max_tokens"] == 222


@pytest.mark.asyncio
async def test_invoke_forwards_kwargs():
    """**kwargs（如 thinking）透传给 adapter.create。"""
    adapter = MagicMock()
    adapter.create = AsyncMock(return_value=_make_response([_text_block("h")]))

    caller = LLMCaller(adapter, model="m")
    await caller.invoke([], thinking={"type": "disabled"})

    kw = adapter.create.call_args.kwargs
    assert kw["thinking"] == {"type": "disabled"}


# ─── invoke 重试路径 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_invoke_retries_on_transient_then_succeeds(monkeypatch):
    """瞬态错误重试后成功。用 monkeypatch 跳过真实 sleep。"""
    import ccserver.model_engine.client as mod
    monkeypatch.setattr(mod.asyncio, "sleep", AsyncMock())

    adapter = MagicMock()
    good = _make_response([_text_block("ok")])
    adapter.create = AsyncMock(
        side_effect=[TransientLLMError("net", None), good]
    )

    caller = LLMCaller(adapter, model="m")
    result = await caller.invoke([])

    assert result is good
    assert adapter.create.await_count == 2


@pytest.mark.asyncio
async def test_invoke_raises_after_exhausting_retries(monkeypatch):
    import ccserver.model_engine.client as mod
    monkeypatch.setattr(mod.asyncio, "sleep", AsyncMock())

    adapter = MagicMock()
    adapter.create = AsyncMock(side_effect=TransientLLMError("net", None))

    caller = LLMCaller(adapter, model="m")
    with pytest.raises(TransientLLMError):
        await caller.invoke([])
    assert adapter.create.await_count == 3  # _MAX_RETRIES


@pytest.mark.asyncio
async def test_invoke_non_transient_raises_immediately():
    """不可重试异常立即上抛，不重试。"""
    adapter = MagicMock()
    adapter.create = AsyncMock(side_effect=ValueError("bad request"))

    caller = LLMCaller(adapter, model="m")
    with pytest.raises(ValueError):
        await caller.invoke([])
    assert adapter.create.await_count == 1


@pytest.mark.asyncio
async def test_invoke_on_retry_callback_invoked(monkeypatch):
    """on_retry 在每次重试前被调用，带 (attempt, error)。"""
    import ccserver.model_engine.client as mod
    monkeypatch.setattr(mod.asyncio, "sleep", AsyncMock())

    adapter = MagicMock()
    good = _make_response([_text_block("ok")])
    adapter.create = AsyncMock(side_effect=[TransientLLMError("e1", None), good])

    seen = []

    async def on_retry(attempt, error):
        seen.append((attempt, type(error).__name__))

    caller = LLMCaller(adapter, model="m")
    await caller.invoke([], on_retry=on_retry)

    assert seen == [(0, "TransientLLMError")]


# ─── extract_text / invoke_text ───────────────────────────────────────────────


def test_extract_text_skips_thinking_block():
    """thinking 在前、text 在后时，取 text。"""
    resp = _make_response([_thinking_block("思考..."), _text_block("答案")])
    assert LLMCaller.extract_text(resp) == "答案"


def test_extract_text_returns_none_when_no_text_block():
    resp = _make_response([_thinking_block("只有思考")])
    assert LLMCaller.extract_text(resp) is None


def test_extract_text_empty_content_returns_none():
    """content 为空列表时，返回 None（新行为，不再 assert）。"""
    resp = _make_response([])
    assert LLMCaller.extract_text(resp) is None


def test_extract_text_unified_response_str_content():
    """新路径：content 是字符串（UnifiedResponse）时直接返回。"""
    resp = MagicMock()
    resp.content = "直接文本"
    assert LLMCaller.extract_text(resp) == "直接文本"


def test_extract_text_unified_response_empty_str_returns_none():
    """新路径：content 是空字符串时返回 None。"""
    resp = MagicMock()
    resp.content = ""
    assert LLMCaller.extract_text(resp) is None


def test_extract_text_no_content_attr_returns_none():
    """response 没有 content 属性时返回 None。"""
    resp = object()  # 没有任何属性
    assert LLMCaller.extract_text(resp) is None


@pytest.mark.asyncio
async def test_invoke_text_returns_first_text():
    adapter = MagicMock()
    adapter.create = AsyncMock(
        return_value=_make_response([_thinking_block("t"), _text_block("正文")])
    )
    caller = LLMCaller(adapter, model="m")
    assert await caller.invoke_text([]) == "正文"


# ─── bind / 无状态用法 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bind_fills_defaults():
    adapter = MagicMock()
    adapter.create = AsyncMock(return_value=_make_response([_text_block("h")]))

    base = LLMCaller(adapter)
    bound = base.bind(model="bound-model", max_tokens=333)
    await bound.invoke([])

    kw = adapter.create.call_args.kwargs
    assert kw["model"] == "bound-model"
    assert kw["max_tokens"] == 333


@pytest.mark.asyncio
async def test_stateless_usage_all_params_per_call():
    """不绑默认值，每次全参数传入。"""
    adapter = MagicMock()
    adapter.create = AsyncMock(return_value=_make_response([_text_block("h")]))

    caller = LLMCaller(adapter)
    await caller.invoke([], model="m1", max_tokens=10)

    kw = adapter.create.call_args.kwargs
    assert kw["model"] == "m1"
    assert kw["max_tokens"] == 10


# ─── stream ───────────────────────────────────────────────────────────────────


class _FakeStreamCtx:
    """
    模拟 adapter.stream(...) 返回的 async context manager。

    chunks: 要逐个 yield 的 UnifiedStreamDelta 对象列表（或在迭代到某 index 时抛异常）。
    final:  get_final_message() 返回值。
    raise_at: 若非 None，迭代到该 index 时抛出 raise_exc。
    """

    def __init__(self, chunks, final, raise_at=None, raise_exc=None):
        self._chunks = chunks
        self._final = final
        self._raise_at = raise_at
        self._raise_exc = raise_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def __aiter__(self):
        # raise_at=0 且 chunks 为空时，在迭代开始前就抛出异常
        if self._raise_at is not None and self._raise_at == 0 and not self._chunks:
            raise self._raise_exc
        for i, c in enumerate(self._chunks):
            if self._raise_at is not None and i == self._raise_at:
                raise self._raise_exc
            yield c

    async def get_final_message(self):
        return self._final


@pytest.mark.asyncio
async def test_stream_emits_text_and_thinking_and_returns_final():
    final = _make_response([_text_block("hello")])
    ctx = _FakeStreamCtx(
        chunks=[
            UnifiedStreamDelta("thinking", "想"),
            UnifiedStreamDelta("text", "he"),
            UnifiedStreamDelta("text", "llo"),
        ],
        final=final,
    )
    adapter = MagicMock()
    adapter.stream = MagicMock(return_value=ctx)

    texts, thinks = [], []

    async def on_text(s):
        texts.append(s)

    async def on_thinking(s):
        thinks.append(s)

    caller = LLMCaller(adapter, model="m")
    result = await caller.stream([], on_text=on_text, on_thinking=on_thinking)

    assert result is final
    assert texts == ["he", "llo"]
    assert thinks == ["想"]


@pytest.mark.asyncio
async def test_stream_retries_before_first_token(monkeypatch):
    """首 token 前抛瞬态错误 → 重试成功。"""
    import ccserver.model_engine.client as mod
    monkeypatch.setattr(mod.asyncio, "sleep", AsyncMock())

    final = _make_response([_text_block("ok")])
    bad_ctx = _FakeStreamCtx(chunks=[], final=None, raise_at=0,
                             raise_exc=TransientLLMError("early", None))
    good_ctx = _FakeStreamCtx(chunks=[UnifiedStreamDelta("text", "ok")], final=final)

    adapter = MagicMock()
    adapter.stream = MagicMock(side_effect=[bad_ctx, good_ctx])

    caller = LLMCaller(adapter, model="m")
    result = await caller.stream([], on_text=AsyncMock())

    assert result is final
    assert adapter.stream.call_count == 2


@pytest.mark.asyncio
async def test_stream_no_retry_after_first_token(monkeypatch):
    """已吐 token 后抛瞬态错误 → 不重试，直接上抛。"""
    import ccserver.model_engine.client as mod
    monkeypatch.setattr(mod.asyncio, "sleep", AsyncMock())

    # 先 yield 一个 text_delta（emitted=True），再在 index=1 抛瞬态错误
    ctx = _FakeStreamCtx(
        chunks=[UnifiedStreamDelta("text", "partial"), None],
        final=None,
        raise_at=1,
        raise_exc=TransientLLMError("mid-stream", None),
    )
    adapter = MagicMock()
    adapter.stream = MagicMock(return_value=ctx)

    emitted = []

    async def on_text(s):
        emitted.append(s)

    caller = LLMCaller(adapter, model="m")
    with pytest.raises(TransientLLMError):
        await caller.stream([], on_text=on_text)

    assert emitted == ["partial"]
    assert adapter.stream.call_count == 1  # 未重试


@pytest.mark.asyncio
async def test_stream_thinking_counts_as_emitted(monkeypatch):
    """thinking_delta 也算已吐 token：之后失败不重试。"""
    import ccserver.model_engine.client as mod
    monkeypatch.setattr(mod.asyncio, "sleep", AsyncMock())

    ctx = _FakeStreamCtx(
        chunks=[UnifiedStreamDelta("thinking", "想"), None],
        final=None,
        raise_at=1,
        raise_exc=TransientLLMError("after-thinking", None),
    )
    adapter = MagicMock()
    adapter.stream = MagicMock(return_value=ctx)

    caller = LLMCaller(adapter, model="m")
    with pytest.raises(TransientLLMError):
        await caller.stream([], on_thinking=AsyncMock())

    assert adapter.stream.call_count == 1


@pytest.mark.asyncio
async def test_stream_forwards_params_to_adapter():
    """stream 把 model/system/tools/max_tokens 正确透传给 adapter.stream。"""
    final = _make_response([_text_block("ok")])
    ctx = _FakeStreamCtx(chunks=[UnifiedStreamDelta("text", "ok")], final=final)
    adapter = MagicMock()
    adapter.stream = MagicMock(return_value=ctx)

    caller = LLMCaller(adapter, model="bound", system="sys", max_tokens=100)
    tools = [{"name": "Bash"}]
    await caller.stream([{"role": "user", "content": "x"}], tools=tools)

    kw = adapter.stream.call_args.kwargs
    assert kw["model"] == "bound"
    assert kw["system"] == "sys"
    assert kw["max_tokens"] == 100
    assert kw["tools"] == tools
    assert kw["messages"] == [{"role": "user", "content": "x"}]
