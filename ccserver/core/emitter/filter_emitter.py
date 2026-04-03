"""
filter_emitter — 根据 output_mode 决定是否将事件转发给底层 Emitter。

四种模式：
  interactive  保留完整事件流（默认行为，等同于不包装）
  final_only   只转发 done 和 error（过滤所有中间事件和 subagent_done）
  streaming    只转发 token、done、error（过滤工具调用事件和 subagent_done）
  verbose      转发所有事件，但 tool_result 不截断（完整输出）
"""

from . import BaseEmitter

VALID_MODES = {"interactive", "final_only", "verbose", "streaming"}


class FilterEmitter(BaseEmitter):
    """
    包装另一个 Emitter，根据 output_mode 决定是否转发每个事件。

    用法：
        raw_emitter = SSEEmitter()
        emitter = FilterEmitter(raw_emitter, mode="final_only")
    """

    def __init__(self, inner: BaseEmitter, mode: str = "interactive"):
        if mode not in VALID_MODES:
            mode = "interactive"
        self._inner = inner
        self._mode = mode

    async def emit(self, event: dict) -> None:
        t = event.get("type", "")

        # permission_request 和 ask_user 始终透传，不受 output_mode 过滤
        # 这两类事件需要客户端感知并响应，过滤掉会导致 agent 永久挂起
        if t in ("permission_request", "ask_user"):
            await self._inner.emit(event)
            return

        if self._mode == "interactive":
            await self._inner.emit(event)

        elif self._mode == "final_only":
            if t in ("done", "error"):
                await self._inner.emit(event)

        elif self._mode == "streaming":
            if t in ("token", "done", "error"):
                await self._inner.emit(event)

        elif self._mode == "verbose":
            await self._inner.emit(event)

    def fmt_tool_result(self, name: str, output: str) -> dict:
        """verbose 模式下不截断输出，其他模式使用父类的 500 字符截断。"""
        if self._mode == "verbose":
            return self._fmt("tool_result", tool=name, output=output)
        return super().fmt_tool_result(name, output)

    async def emit_ask_user(self, questions: list) -> str:
        """委托给内部 emitter，确保阻塞等待逻辑正确执行。"""
        return await self._inner.emit_ask_user(questions)

    async def emit_permission_request(self, tool_name: str, tool_input: dict) -> bool:
        """委托给内部 emitter，确保阻塞等待逻辑正确执行。"""
        return await self._inner.emit_permission_request(tool_name, tool_input)
