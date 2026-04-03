"""
Bash tool — aligned with Claude Code's Bash tool conventions.
"""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from .bt_base import BaseTool, ToolParam, ToolResult

if TYPE_CHECKING:
    from ..settings import ProjectSettings


class BTBash(BaseTool):

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
                "Returns the process PID. Use for dev servers, watchers, or long-running daemons. "
                "Default: false."
            ),
            required=False,
        ),
    }

    # 硬编码兜底黑名单：无论任何配置都拒绝，防止最危险的操作
    _HARDBLOCK = ["rm -rf /", "shutdown", "reboot", "> /dev/"]

    def __init__(self, workdir: Path, settings: "ProjectSettings"):
        self.workdir = workdir
        self.settings = settings

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

        logger.debug("Bash | cmd={!r} timeout={}s background={}", command[:120], timeout_sec, run_in_background)
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(self.workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            if run_in_background:
                logger.debug("Bash background | pid={}", proc.pid)
                return ToolResult.ok(f"Process started in background (pid {proc.pid})")

            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
            except asyncio.TimeoutError:
                proc.kill()
                logger.warning("Bash timeout | cmd={!r} timeout={}s", command[:80], timeout_sec)
                return ToolResult.error(f"Command timed out after {timeout_sec:.0f}s")

            output = stdout.decode(errors="replace").strip()
            content = output[:50_000] or "(empty output)"

            if proc.returncode != 0:
                logger.warning("Bash non-zero exit | rc={} cmd={!r}", proc.returncode, command[:80])
                return ToolResult(content=content, is_error=True)

            logger.debug("Bash ok | rc=0 output_len={}", len(content))
            return ToolResult.ok(content)

        except Exception as e:
            logger.error("Bash exception | cmd={!r} error={}", command[:80], e)
            return ToolResult.error(str(e))
