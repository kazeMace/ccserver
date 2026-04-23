from abc import ABC, abstractmethod


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

    # ── 任务生命周期事件 ─────────────────────────────────────────────────────

    def fmt_task_started(
        self,
        task_id: str,
        task_type: str,
        description: str = "",
        pid: int | None = None,
    ) -> dict:
        """
        格式化 task_started 事件。

        触发时机：Bash / Agent 后台任务刚启动时。
        客户端收到此事件后开始渲染任务 UI。

        Args:
            task_id:    任务唯一 ID（格式 "b{hex}" 或 "a{hex}"）。
            task_type:  任务类型，对应 ShellTaskState.to_dict()["type"]。
            description: 人类可读描述（命令内容或 agent prompt）。
            pid:        操作系统进程 ID（shell 任务有，agent 任务无）。

        Example:
            {"type": "task_started", "task_id": "b3f2a1c0", "task_type": "local_bash",
             "description": "npm run build", "pid": 12345}
        """
        return self._fmt(
            "task_started",
            task_id=task_id,
            task_type=task_type,
            description=description,
            pid=pid,
        )

    def fmt_task_progress(
        self,
        task_id: str,
        status: str,
        output: str = "",
        progress: dict | None = None,
    ) -> dict:
        """
        格式化 task_progress 事件。

        触发时机：后台任务运行期间，周期性推送（如每秒轮询时）。
        output 为增量输出（自上一次 task_progress 以来的新增内容）。

        Args:
            task_id:   任务 ID。
            status:    当前状态（"running" / "pending"）。
            output:    增量输出内容（截断到 2000 字符，防止过大）。
            progress:  可选进度信息（Agent 任务使用，如 toolUseCount）。

        Example:
            {"type": "task_progress", "task_id": "b3f2a1c0", "status": "running",
             "output": "Compiling...\n", "progress": null}
        """
        # 限制单次推送的输出量，避免 event 过大
        return self._fmt(
            "task_progress",
            task_id=task_id,
            status=status,
            output=output[:2000] if output else "",
            progress=progress,
        )

    def fmt_task_done(
        self,
        task_id: str,
        status: str,
        output: str = "",
        exit_code: int | None = None,
        reason: str | None = None,
    ) -> dict:
        """
        格式化 task_done 事件。

        触发时机：后台任务进入 completed / failed / killed 状态时。
        客户端收到此事件后更新任务 UI（变为完成状态）。

        Args:
            task_id:   任务 ID。
            status:    最终状态（"completed" / "failed" / "killed"）。
            output:    完整输出（截断到 50KB，与工具返回值一致）。
            exit_code: 进程退出码（completed=0, failed≠0, killed=-1 或 null）。
            reason:    失败/终止原因描述。

        Example:
            {"type": "task_done", "task_id": "b3f2a1c0", "status": "completed",
             "output": "Build succeeded.", "exit_code": 0, "reason": null}
        """
        return self._fmt(
            "task_done",
            task_id=task_id,
            status=status,
            output=output[:50_000] if output else "",
            exit_code=exit_code,
            reason=reason,
        )

    @abstractmethod
    async def emit(self, event: dict) -> None: ...

    async def _emit_with_hook(self, event: dict, session=None) -> dict:
        """
        带 hook 的 emit 包装。

        hook: message:outbound:sending — 发送前拦截（observing）
        hook 可用于日志记录、事件追踪等，不修改 event 内容。

        子类可覆盖此方法以接入 session 并触发 hook。
        默认实现：直接返回原 event。
        """
        if session is not None:
            try:
                from ccserver.managers.hooks import HookContext
                ctx = HookContext(
                    session_id=session.id,
                    workdir=session.workdir,
                    project_root=session.project_root,
                    depth=0,
                    agent_id="emitter",
                    agent_name=None,
                    is_orchestrator=True,
                )
                import asyncio
                asyncio.create_task(
                    session.hooks.emit_void(
                        "message:outbound:sending",
                        {"event": event},
                        ctx,
                    )
                )
            except Exception:
                pass  # hook 出错不影响主流程
        return event

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

    # ── 任务生命周期 emit（异步 convenience 包装）──────────────────────────────

    async def emit_task_started(
        self,
        task_id: str,
        task_type: str,
        description: str = "",
        pid: int | None = None,
    ):
        """推送 task_started 事件。参见 fmt_task_started。"""
        await self.emit(self.fmt_task_started(task_id, task_type, description, pid))

    async def emit_task_progress(
        self,
        task_id: str,
        status: str,
        output: str = "",
        progress: dict | None = None,
    ):
        """推送 task_progress 事件。参见 fmt_task_progress。"""
        await self.emit(self.fmt_task_progress(task_id, status, output, progress))

    async def emit_task_done(
        self,
        task_id: str,
        status: str,
        output: str = "",
        exit_code: int | None = None,
        reason: str | None = None,
    ):
        """推送 task_done 事件。参见 fmt_task_done。"""
        await self.emit(self.fmt_task_done(task_id, status, output, exit_code, reason))
