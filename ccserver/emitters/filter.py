from .base import BaseEmitter

# verbosity 合法值
VALID_VERBOSITY = {"final_only", "verbose"}


class FilterEmitter(BaseEmitter):
    """
    包装另一个 Emitter，根据三个独立参数过滤事件：

    verbosity   : "verbose"    — 全部中间过程，tool_result 不截断
                  "final_only" — 只有 done/error，强制 interactive=False
    stream      : True  — 透传 token 事件
                  False — 屏蔽 token 事件
    interactive : True  — ask_user / permission_request 透传给客户端，阻塞等待
                  False — 直接短路：ask_user 返回空字符串，permission_request 返回 False

    约束：verbosity="final_only" 时强制 interactive=False。

    用法：
        raw = SSEEmitter()
        emitter = FilterEmitter(raw, verbosity="verbose", stream=True, interactive=True)
    """

    def __init__(
        self,
        inner: BaseEmitter,
        verbosity: str = "verbose",
        stream: bool = True,
        interactive: bool = True,
    ):
        # 校正非法 verbosity 值
        if verbosity not in VALID_VERBOSITY:
            verbosity = "verbose"

        # final_only 强制关闭交互：客户端不接收中间事件，ask_user/permission 无从响应
        if verbosity == "final_only":
            interactive = False

        self._inner = inner
        self._verbosity = verbosity
        self._stream = stream
        self._interactive = interactive

    async def emit(self, event: dict) -> None:
        t = event.get("type", "")

        # ask_user / permission_request 由 emit_ask_user / emit_permission_request 处理
        # 此处不再单独过滤，避免与阻塞等待逻辑冲突
        if t in ("ask_user", "permission_request"):
            return

        # token 事件：由 stream 开关决定
        if t == "token":
            if self._stream:
                await self._inner.emit(event)
            return

        # final_only：只透传 done / error
        if self._verbosity == "final_only":
            if t in ("done", "error"):
                await self._inner.emit(event)
            return

        # verbose：全部透传
        await self._inner.emit(event)

    def fmt_tool_result(self, name: str, output: str) -> dict:
        """verbose 模式下不截断输出；final_only 用不到 tool_result，保持父类截断即可。"""
        if self._verbosity == "verbose":
            return self._fmt("tool_result", tool=name, output=output)
        return super().fmt_tool_result(name, output)

    async def emit_ask_user(self, questions: list) -> str:
        """
        interactive=True  → 委托给内部 emitter，阻塞等待用户回答。
        interactive=False → 直接返回空字符串，不推送事件，不阻塞。
        """
        if not self._interactive:
            return ""
        return await self._inner.emit_ask_user(questions)

    async def emit_permission_request(self, tool_name: str, tool_input: dict) -> bool:
        """
        interactive=True  → 委托给内部 emitter，阻塞等待用户决定。
        interactive=False → 直接返回 False（拒绝），不推送事件，不阻塞。
        """
        if not self._interactive:
            return False
        return await self._inner.emit_permission_request(tool_name, tool_input)
