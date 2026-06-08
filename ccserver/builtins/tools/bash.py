"""
Bash tool — 跨平台 Shell 执行工具。

平台分发逻辑：
- macOS / Linux：使用 bash -c <command>
- Windows：使用 powershell -NonInteractive -Command <command>

危险命令黑名单在各平台均有对应规则，见 _check_hardblock()。
"""

import asyncio
import platform as platform_module
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from .base import BuiltinTools, ToolParam, ToolResult

if TYPE_CHECKING:
    from ccserver.session import Session
    from ccserver.emitters.base import BaseEmitter
    from ccserver.tasks import ShellTaskState
else:
    # 运行时导入，避免循环：session.py → tasks → bash → session
    # session.py 在末尾才导入 bash.py，所以这里安全
    from ccserver.session import Session

from ccserver.settings import ProjectSettings


class BTBash(BuiltinTools):

    name = "Bash"
    risk = "high"
    tags = ["shell", "system"]

    description = (
        "Execute a shell command in the workspace. "
        "Use for running tests, builds, git operations, package installs, "
        "file system operations, or any system task that has no dedicated tool. "
        "On macOS/Linux runs via bash; on Windows runs via PowerShell. "
        "Do NOT use for file searches — use Glob (find by name pattern) or "
        "Grep (find by content) tools instead; they are faster and safer. "
        "Output is capped at 50 KB; pipe through head/tail (Unix) or Select-Object (PowerShell) to limit large outputs."
    )

    params = {
        "command": ToolParam(
            type="string",
            description=(
                "The shell command to execute. "
                "On macOS/Linux: bash syntax (&&, ;, pipes, env vars with $VAR). "
                "On Windows: PowerShell syntax (semicolons to chain, $env:VAR for env vars). "
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

    def _check_hardblock(self, cmd: str) -> ToolResult | None:
        """
        检查命令是否命中硬编码黑名单（跨平台）。

        Unix / Windows 规则均在此函数中处理：
        - Unix：rm -rf /、shutdown、reboot、写入 /dev 设备等
        - Windows：rd /s /q C:\、format C:、Stop-Computer 等

        采用基于 shlex 分词的 token 级精确匹配，防止通过空格、
        引号、换行、sudo 前缀、完整路径等手法绕过。

        同时递归检查 shell wrapper 的 -c / -Command 参数内容。
        """
        try:
            tokens = shlex.split(cmd)
        except ValueError:
            # shlex 分词失败（如未闭合引号），退回到空格分割
            tokens = cmd.split()

        if not tokens:
            return None

        # ── 跳过特权前缀（sudo/su/doas），定位实际命令 ──
        cmd_idx = 0
        while cmd_idx < len(tokens) and tokens[cmd_idx].lower() in (
            "sudo", "su", "doas", "nice", "env", "time",
        ):
            cmd_idx += 1
        if cmd_idx >= len(tokens):
            return None

        # 处理前缀命令自身的 -c 参数（如 su -c "rm -rf /"）
        if tokens[cmd_idx] == "-c" and cmd_idx + 1 < len(tokens):
            inner_result = self._check_hardblock(tokens[cmd_idx + 1])
            if inner_result:
                return inner_result
            return None

        actual_cmd = tokens[cmd_idx].lower()
        # 处理完整路径：/bin/rm → rm，C:\Windows\System32\cmd.exe → cmd.exe
        actual_cmd = Path(actual_cmd).name
        # 去掉 Windows 可执行文件扩展名：cmd.exe → cmd，powershell.exe → powershell
        if actual_cmd.endswith(".exe"):
            actual_cmd = actual_cmd[:-4]
        args = tokens[cmd_idx + 1:]

        # ── Unix: rm + 递归 + 根目录 ──────────────────────────────────────────
        if actual_cmd == "rm":
            try:
                dd_idx = args.index("--")
                args_after_dd = args[dd_idx + 1:]
                options = args[:dd_idx]
            except ValueError:
                args_after_dd = args
                options = args

            has_recursive = any(
                t.startswith("-") and "r" in t for t in options
            )
            has_root = any(t == "/" or t == "/." for t in args_after_dd)
            if has_recursive and has_root:
                return ToolResult.error("Blocked: recursive delete root filesystem")

        # ── Unix: 系统控制命令 ─────────────────────────────────────────────────
        if actual_cmd in ("shutdown", "reboot", "poweroff", "halt", "init"):
            return ToolResult.error(
                f"Blocked: system control command '{actual_cmd}'"
            )

        # ── Unix: 重定向到系统设备 ─────────────────────────────────────────────
        # 如 "> /dev/sda"、"dd if=/dev/zero of=/dev/sda"
        for i, t in enumerate(tokens):
            if t in (">", ">>", "1>", "1>>", "2>", "2>>"):
                if i + 1 < len(tokens) and tokens[i + 1].startswith("/dev/"):
                    return ToolResult.error("Blocked: redirect to system device")
            if t.startswith("of=/dev/") or t.startswith("if=/dev/"):
                return ToolResult.error("Blocked: direct device access")

        # ── Windows: rd /s /q + 根目录或系统盘 ────────────────────────────────
        # rd /s /q C:\ 或 rmdir /s /q C:\
        if actual_cmd in ("rd", "rmdir"):
            args_lower = [a.lower() for a in args]
            has_s = "/s" in args_lower or "-s" in args_lower
            # 检测目标是否指向盘根（C:\、D:\ 等）
            has_drive_root = any(
                len(a) <= 4 and len(a) >= 2 and a[1] in (":", ":\\")
                for a in args
            )
            if has_s and has_drive_root:
                return ToolResult.error("Blocked: recursive delete drive root (rd /s)")

        # ── Windows: format 命令（格式化磁盘） ────────────────────────────────
        if actual_cmd == "format":
            return ToolResult.error("Blocked: disk format command")

        # ── Windows: PowerShell 系统控制 Cmdlet ──────────────────────────────
        # Stop-Computer（关机）、Restart-Computer（重启）
        if actual_cmd in ("stop-computer", "restart-computer"):
            return ToolResult.error(
                f"Blocked: system control cmdlet '{actual_cmd}'"
            )

        # ── Windows: reg delete 删除注册表根键 ────────────────────────────────
        if actual_cmd == "reg":
            args_lower = [a.lower() for a in args]
            if args_lower and args_lower[0] == "delete":
                # 检测是否操作注册表根键（HKLM\\ 或 HKCU\\ 等两级以内）
                root_keys = ("hklm", "hkcu", "hkcr", "hku", "hkcc",
                             "hkey_local_machine", "hkey_current_user",
                             "hkey_classes_root", "hkey_users",
                             "hkey_current_config")
                if len(args_lower) > 1 and any(
                    args_lower[1].startswith(r) for r in root_keys
                ):
                    # 只有路径深度 <= 1（即操作根键本身）才拦截
                    path_parts = args_lower[1].replace("/", "\\").split("\\")
                    if len(path_parts) <= 2:
                        return ToolResult.error(
                            "Blocked: deleting registry root key"
                        )

        # ── Unix: 递归检查 shell wrapper 的 -c 参数 ───────────────────────────
        if actual_cmd in ("sh", "bash", "zsh", "dash", "ksh", "fish"):
            try:
                c_idx = tokens.index("-c")
                if c_idx + 1 < len(tokens):
                    inner_result = self._check_hardblock(tokens[c_idx + 1])
                    if inner_result:
                        return inner_result
            except ValueError:
                pass

        # ── Windows: 递归检查 powershell -Command / -c 内容 ──────────────────
        if actual_cmd in ("powershell", "pwsh"):
            for flag in ("-command", "-c"):
                try:
                    flag_idx = [t.lower() for t in tokens].index(flag)
                    if flag_idx + 1 < len(tokens):
                        inner_result = self._check_hardblock(tokens[flag_idx + 1])
                        if inner_result:
                            return inner_result
                    break
                except ValueError:
                    pass

        return None

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

    @staticmethod
    def _is_windows() -> bool:
        """检测当前是否运行在 Windows 上。"""
        return platform_module.system() == "Windows"

    @staticmethod
    def _build_exec_argv(command: str) -> list[str]:
        """
        根据当前平台构造 subprocess exec 参数列表。

        - macOS / Linux：["bash", "-c", command]
        - Windows：["powershell", "-NonInteractive", "-Command", command]

        使用 exec 模式（create_subprocess_exec）而非 shell=True，
        可以明确指定解释器，避免平台默认 shell 行为差异。

        Args:
            command: 用户传入的 shell 命令字符串

        Returns:
            可直接传给 create_subprocess_exec 的 argv 列表
        """
        if platform_module.system() == "Windows":
            # PowerShell 比 cmd.exe 更现代，支持管道、&&、字符串操作等
            # -NonInteractive：禁止交互提示（如 profile 加载、确认框）
            # -Command：后接命令字符串，等价于 bash 的 -c
            return ["powershell", "-NonInteractive", "-Command", command]
        else:
            # macOS 和 Linux 统一使用 bash
            # 大多数系统均预装 bash，兼容性最好
            return ["bash", "-c", command]

    async def validate(self, **kwargs) -> ToolResult | None:
        error = await super().validate(**kwargs)
        if error:
            return error
        command = kwargs.get("command", "")
        cmd = command.strip()

        # 硬编码兜底黑名单（最高优先级，不可被配置覆盖）
        # 使用 shlex 分词后检查，防止简单绕过（如空格、引号包裹）
        blocked_result = self._check_hardblock(cmd)
        if blocked_result:
            return blocked_result

        # 运行时从 settings 读取 allow/deny，支持动态变更
        if not self.settings.is_command_allowed("Bash", cmd):
            denied_prefixes = self.settings.denied_commands.get("Bash", [])
            allowed_prefixes = (
                self.settings.allowed_commands.get("Bash") if self.settings.allowed_commands else None
            )
            # 判断是被 deny 命中，还是 allow 白名单未命中
            hit_deny = any(cmd.startswith(p) for p in denied_prefixes)
            reason = "matched deny prefix" if hit_deny else f"not in allow whitelist {allowed_prefixes}"
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
        argv = self._build_exec_argv(command)
        proc = await asyncio.create_subprocess_exec(
            *argv,
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

        # 2. 构造任务状态（pending），稍后 mark_running 填充进程信息
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

        # 4. 启动 subprocess（使用 exec 模式，明确指定平台解释器）
        argv = self._build_exec_argv(command)
        proc = await asyncio.create_subprocess_exec(
            *argv,
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
                    # 只推送本轮新增的增量（read_incremental 自动处理 offset）
                    delta = task.read_incremental()
                    if delta and self._emitter:
                        await self._emitter.emit_task_progress(
                            task_id=task.id,
                            status="running",
                            output=delta,
                        )

            # 进程结束：读取可能残留的未读数据
            try:
                remaining = await proc.stdout.read()
            except Exception:
                remaining = b""
            if remaining:
                decoded = remaining.decode(errors="replace")
                task.append_output(decoded)
                delta = task.read_incremental()
                if delta and self._emitter:
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
