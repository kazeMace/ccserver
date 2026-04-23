"""
Bash tool — aligned with Claude Code's Bash tool conventions.
"""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from .base import BuiltinTools, ToolParam, ToolResult

if TYPE_CHECKING:
    from ccserver.session import Session
    from ccserver.tasks import ShellTaskRegistry
    from ccserver.emitters.base import BaseEmitter
else:
    # 运行时导入，避免循环：session.py → tasks → bash → session
    # session.py 在末尾才导入 bash.py，所以这里安全
    from ccserver.session import Session

from ccserver.settings import ProjectSettings


class BTBash(BuiltinTools):

    name = "Bash"

    description = (
        "Execute a shell command in the workspace. "
        "Use for running tests, builds, git operations, package installs, "
        "file system operations, or any system task that has no dedicated tool. "
        "Do NOT use for file searches — use Glob (find by name pattern) or "
        "Grep (find by content) tools instead; they are faster and safer. "
        "Output is capped at 50 KB; pipe through head/tail to limit large outputs."
    )

    params = {
        "command": ToolParam(
            type="string",
            description=(
                "The shell command to execute. "
                "Runs in bash with cwd set to the workspace root. "
                "Chain multiple commands with && (stop on failure) or ; (continue on failure). "
                "Avoid interactive commands that prompt for input — they will hang until timeout. "
                "For file searches, prefer Glob/Grep tools over find/grep shell commands."
            ),
        ),
        "description": ToolParam(
            type="string",
            description=(
                "A short human-readable label for what this command does. "
                "Shown in the UI while the command is running. "
                "Examples: 'Run unit tests', 'Install dependencies', 'Show git log'."
            ),
            required=False,
        ),
        "timeout": ToolParam(
            type="integer",
            description=(
                "Maximum time in milliseconds before the process is killed. "
                "Default: 120000 (2 minutes). "
                "Increase for slow builds, large test suites, or network-heavy operations."
            ),
            required=False,
        ),
        "run_in_background": ToolParam(
            type="boolean",
            description=(
                "If true, start the process and return immediately without waiting for it to finish. "
                "Returns the background task ID and PID. Use for dev servers, watchers, or long-running daemons. "
                "Default: false."
            ),
            required=False,
        ),
    }

    # 硬编码兜底黑名单：无论任何配置都拒绝，防止最危险的操作
    _HARDBLOCK = ["rm -rf /", "shutdown", "reboot", "> /dev/"]

    def __init__(
        self,
        workdir: Path,
        settings: "ProjectSettings",
        session: "Session | None" = None,
        emitter: "BaseEmitter | None" = None,
    ):
        """
        Args:
            workdir: 命令执行的工作目录。
            settings: 项目配置（用于命令 allow/deny 检查）。
            session: Session 实例，提供 shell_tasks 注册表。
                     不传则后台任务无法注册，但仍能执行。
            emitter: BaseEmitter，用于推送 task_started / task_done 事件。
                     通常由 AgentRunner 在 runner.run() 时通过 factory 注入。
                     不传则跳过事件推送，不阻断后台任务执行。
        """
        self.workdir = workdir
        self.settings = settings
        self._session = session
        self._emitter = emitter

    @property
    def _shell_tasks(self):
        """Session 的 ShellTaskRegistry，无 session 时返回 None。"""
        if self._session is None:
            return None
        return self._session.shell_tasks

    async def validate(self, **kwargs) -> ToolResult | None:
        error = await super().validate(**kwargs)
        if error:
            return error
        command = kwargs.get("command", "")
        cmd = command.strip()

        # 硬编码兜底黑名单（最高优先级，不可被配置覆盖）
        for blocked in self._HARDBLOCK:
            if blocked in cmd:
                return ToolResult.error(f"Blocked command pattern: '{blocked}'")

        # 运行时从 settings 读取 allow/deny，支持动态变更
        if not self.settings.is_command_allowed("Bash", cmd):
            denied_prefixes = self.settings.denied_commands.get("Bash", [])
            allowed_prefixes = (
                self.settings.allowed_commands.get("Bash") if self.settings.allowed_commands else None
            )
            # 判断是被 deny 命中，还是 allow 白名单未命中
            hit_deny = any(cmd.startswith(p) for p in denied_prefixes)
            reason = f"matched deny prefix" if hit_deny else f"not in allow whitelist {allowed_prefixes}"
            logger.warning(
                "Bash blocked | reason={} cmd={!r} denied={} allowed={}",
                reason, cmd[:120], denied_prefixes, allowed_prefixes,
            )
            return ToolResult.error(f"Command not allowed by settings: '{cmd}'")

        return None

    async def run(
        self,
        command: str,
        description: str = "",
        timeout: int = 120_000,
        run_in_background: bool = False,
    ) -> ToolResult:
        timeout_sec = timeout / 1000

        logger.debug(
            "Bash | cmd={!r} timeout={}s background={}",
            command[:120], timeout_sec, run_in_background
        )
        try:
            # 后台任务路径：注册 → 启动 → 立即返回
            if run_in_background:
                return await self._run_background(command, description)

            # 前台任务路径：等待 proc 完成 → 返回结果
            return await self._run_foreground(command, timeout_sec)

        except Exception as e:
            logger.error("Bash exception | cmd={!r} error={}", command[:80], e)
            return ToolResult.error(str(e))

    # ── 前台执行路径 ───────────────────────────────────────────────────────────

    async def _run_foreground(
        self,
        command: str,
        timeout_sec: float,
    ) -> ToolResult:
        """
        前台执行：等待 subprocess 完成，返回标准输出和退出码。
        这是 run_in_background=False 时的执行路径。
        """
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(self.workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            proc.kill()
            logger.warning(
                "Bash timeout | cmd={!r} timeout={}s",
                command[:80], timeout_sec
            )
            return ToolResult.error(f"Command timed out after {timeout_sec:.0f}s")

        output = stdout.decode(errors="replace").strip()
        content = output[:50_000] or "(empty output)"

        if proc.returncode != 0:
            logger.warning(
                "Bash non-zero exit | rc={} cmd={!r}",
                proc.returncode, command[:80]
            )
            return ToolResult(content=content, is_error=True)

        logger.debug("Bash ok | rc=0 output_len={}", len(content))
        return ToolResult.ok(content)

    # ── 后台执行路径 ──────────────────────────────────────────────────────────

    async def _run_background(
        self,
        command: str,
        description: str,
    ) -> ToolResult:
        """
        后台执行：启动 subprocess，立即返回 task_id。

        流程：
            1. 生成 task_id
            2. 创建 ShellTaskState，注册到 Session._shell_tasks
            3. 启动 subprocess
            4. 注册 done 回调：进程结束时自动更新状态
            5. 立即返回 ToolResult(background_task_id)

        外部可通过 session.shell_tasks.get(task_id) 查询任务状态。
        """
        # 1. 生成任务 ID
        from ccserver.tasks import generate_shell_id, ShellTaskState

        task_id = generate_shell_id()

        # 2. 构造任务状态（pending）
        # proc_started 稍后在 _register_background_done 中被填充
        task = ShellTaskState(
            id=task_id,
            command=command,
            description=description,
        )

        # 3. 注册到 Session（若 shell_tasks 未注入则跳过注册，但继续执行）
        if self._shell_tasks is not None:
            self._shell_tasks.register(task)
            logger.debug(
                "Bash background registered | task_id={} cmd={!r}",
                task_id, command[:80]
            )
        else:
            logger.warning(
                "Bash background: shell_tasks not injected — task {} will not be tracked. "
                "Pass shell_tasks=session.shell_tasks to BTBash constructor.",
                task_id
            )

        # 4. 启动 subprocess
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(self.workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        # 5. 填充进程信息
        task.mark_running(pid=proc.pid, proc=proc)

        # 6. 向 SSE/WebSocket 推送 task_started 事件
        if self._emitter is not None:
            await self._emitter.emit_task_started(
                task_id=task_id,
                task_type="local_bash",
                description=description or command,
                pid=proc.pid,
            )

        # 7. 启动异步完成监听（不阻塞工具返回值）
        asyncio.create_task(
            self._wait_and_update_task(task, proc)
        )

        # 8. 立即返回，携带 task_id
        logger.debug(
            "Bash background started | task_id={} pid={}",
            task_id, proc.pid
        )
        return ToolResult.ok(
            f"Process started in background (task_id={task_id}, pid={proc.pid})"
        )

    async def _wait_and_update_task(
        self,
        task: "ShellTaskState",
        proc: asyncio.subprocess.Process,
    ) -> None:
        """
        异步等待 subprocess 完成，更新任务状态。

        采用轮询模式（对齐 Claude Code 的 pollTasks 机制）：
          - 每秒读取一次 stdout 的增量输出，推送 task_progress 事件
          - 进程结束时读取残留数据，再推送 task_done 事件

        注意：stdout 全量读取后再追加到 task.output，
        增量输出轮询（pollTasks）留待 Step 3 实现。
        """
        offset = 0  # 记录本次轮询开始前的 output_offset
        try:
            while True:
                # 检查进程是否已结束
                try:
                    # 用 wait_for 每秒检查一次，避免纯 spin-wait
                    await asyncio.wait_for(proc.wait(), timeout=1.0)
                    break  # 进程已结束，退出循环
                except asyncio.TimeoutError:
                    pass  # 进程仍在运行，继续读输出

                # 读取本轮增量输出
                try:
                    chunk = await asyncio.wait_for(
                        proc.stdout.read(4096), timeout=0.5
                    )
                except asyncio.TimeoutError:
                    chunk = b""

                if chunk:
                    decoded = chunk.decode(errors="replace")
                    task.append_output(decoded)
                    # 只推送本轮新增的增量
                    delta = task.output[offset:]
                    if self._emitter:
                        await self._emitter.emit_task_progress(
                            task_id=task.id,
                            status="running",
                            output=delta,
                        )
                    offset = task.output_offset

            # 进程结束：读取可能残留的未读数据
            try:
                remaining = await proc.stdout.read()
            except Exception:
                remaining = b""
            if remaining:
                decoded = remaining.decode(errors="replace")
                task.append_output(decoded)
                delta = task.output[offset:]
                if self._emitter:
                    await self._emitter.emit_task_progress(
                        task_id=task.id,
                        status="running",
                        output=delta,
                    )

            # 读取完整 output 用于 task_done
            full_output = task.output

            if proc.returncode == 0:
                task.mark_completed(exit_code=0)
                logger.info(
                    "Bash background done (success) | task_id={} exit_code=0 output_len={}",
                    task.id, len(full_output)
                )
            else:
                task.mark_failed(
                    exit_code=proc.returncode or -1,
                    reason=f"exit code {proc.returncode}"
                )
                logger.warning(
                    "Bash background done (failed) | task_id={} exit_code={}",
                    task.id, proc.returncode
                )

            # 向 SSE/WebSocket 推送 task_done 事件
            if self._emitter is not None:
                await self._emitter.emit_task_done(
                    task_id=task.id,
                    status=task.status,
                    output=full_output,
                    exit_code=task.exit_code,
                    reason=task.reason,
                )
        except asyncio.CancelledError:
            # 任务被主动 kill，mark_killed 由 registry.kill() 调用者处理
            logger.debug("Bash background task cancelled | task_id={}", task.id)
        except Exception as e:
            # 未知异常，标记为 failed
            task.mark_failed(exit_code=-1, reason=f"exception: {e}")
            logger.error(
                "Bash background task exception | task_id={} error={}",
                task.id, e
            )
