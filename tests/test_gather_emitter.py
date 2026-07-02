"""tests/test_gather_emitter.py — 验证 push 式自定义 emitter 逐 token gather（设计 §8.2）。

逐 token 分析 / gather 的诉求由既有 emitter 抽象（push）覆盖：
自定义一个 BaseEmitter 子类，在 emit 里自缓存 + 分析，无需任何新基础设施。
"""

import pytest

from ccserver.emitters.base import BaseEmitter


class GatherEmitter(BaseEmitter):
    """push 式：Agent 每吐一个 token 就回调进来，自缓存累积。"""

    def __init__(self):
        self.buffer = []

    async def emit(self, event: dict) -> None:
        # 只累积 token 事件；其它事件（thinking / tool_start / done 等）忽略
        if event.get("type") == "token":
            self.buffer.append(event["content"])

    def result(self) -> str:
        return "".join(self.buffer)


@pytest.mark.asyncio
async def test_gather_emitter_accumulates_tokens():
    g = GatherEmitter()
    await g.emit_token("he")
    await g.emit_token("llo")
    assert g.result() == "hello"


@pytest.mark.asyncio
async def test_gather_emitter_ignores_non_token():
    g = GatherEmitter()
    await g.emit_token("x")
    await g.emit_tool_start("Bash", "ls")  # 非 token，不进 buffer
    assert g.result() == "x"
