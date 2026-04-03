from abc import ABC, abstractmethod


# ─── Emitter ──────────────────────────────────────────────────────────────────


class BaseEmitter(ABC):
    """
    所有输出渠道共享的事件格式化基类。
    SSEEmitter、WSEmitter、CollectEmitter 等均继承 fmt_* 方法，
    只需实现 emit() 即可。
    """

    def _fmt(self, type_: str, **kwargs) -> dict:
        return {"type": type_, **kwargs}

    def fmt_token(self, text: str) -> dict:
        return self._fmt("token", content=text)

    def fmt_tool_start(self, name: str, preview: str) -> dict:
        return self._fmt("tool_start", tool=name, preview=preview)

    def fmt_tool_result(self, name: str, output: str) -> dict:
        return self._fmt("tool_result", tool=name, output=output[:500])

    def fmt_subagent_done(self, content: str) -> dict:
        return self._fmt("subagent_done", content=content)

    def fmt_done(self, content: str) -> dict:
        return self._fmt("done", content=content)

    def fmt_error(self, message: str) -> dict:
        return self._fmt("error", message=message)

    def fmt_compact(self, reason: str) -> dict:
        return self._fmt("compact", reason=reason)

    def fmt_ask_user(self, questions: list) -> dict:
        return self._fmt("ask_user", questions=questions)

    def fmt_permission_request(self, tool_name: str, tool_input: dict) -> dict:
        return self._fmt("permission_request", tool=tool_name, input=tool_input)

    @abstractmethod
    async def emit(self, event: dict) -> None: ...

    async def emit_token(self, text: str):
        await self.emit(self.fmt_token(text))

    async def emit_tool_start(self, name: str, preview: str):
        await self.emit(self.fmt_tool_start(name, preview))

    async def emit_tool_result(self, name: str, output: str):
        await self.emit(self.fmt_tool_result(name, output))

    async def emit_subagent_done(self, content: str):
        await self.emit(self.fmt_subagent_done(content))

    async def emit_done(self, content: str):
        await self.emit(self.fmt_done(content))

    async def emit_error(self, message: str):
        await self.emit(self.fmt_error(message))

    async def emit_compact(self, reason: str):
        await self.emit(self.fmt_compact(reason))

    async def emit_ask_user(self, questions: list) -> str:
        """
        向客户端推送提问事件，等待用户回答，返回答案字符串。

        子类必须重写此方法以实现真正的等待。
        默认实现：只推送事件，不等待，立即返回空字符串。
        适用于不支持交互的场景（如 CollectEmitter）。
        """
        await self.emit(self.fmt_ask_user(questions))
        return ""

    async def emit_permission_request(self, tool_name: str, tool_input: dict) -> bool:
        """
        向客户端推送工具权限确认请求，等待用户批准或拒绝。
        返回 True 表示用户批准，False 表示用户拒绝或超时。

        默认实现：只推送事件，立即返回 False（拒绝）。
        适用于不支持交互的场景（CollectEmitter、默认 BaseEmitter）。

        SSEEmitter / WSEmitter 需重写此方法以实现真正的等待交互。
        """
        await self.emit(self.fmt_permission_request(tool_name, tool_input))
        return False
