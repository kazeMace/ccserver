"""
builtins/tools/cron_create.py — BTCronCreate 内置工具。

调度一个定时任务（一次性或循环），到期后将 prompt 注入 Agent inbox。
参数对齐 tools.json schema：
    cron      — 5 字段 cron 表达式（必填）
    prompt    — 触发时注入的 prompt（必填）
    recurring — True=循环（默认），False=一次性
    durable   — True=写磁盘重启后可恢复（默认 False）
"""

from datetime import datetime

from loguru import logger

from .base import BuiltinTools, ToolParam, ToolResult
from ccserver.managers.cron import CronScheduler


class BTCronCreate(BuiltinTools):

    name = "CronCreate"

    description = (
        "Schedule a prompt to be enqueued at a future time. Use for both recurring "
        "schedules and one-shot reminders.\n"
        "Uses standard 5-field cron in the user's local timezone: minute hour day-of-month "
        "month day-of-week. \"0 9 * * *\" means 9am local — no timezone conversion needed.\n"
        "## One-shot tasks (recurring: false)\n"
        "For \"remind me at X\" or \"at <time>, do Y\" requests — fire once then auto-delete.\n"
        "## Recurring jobs (recurring: true, the default)\n"
        "For \"every N minutes\" / \"every hour\" / \"weekdays at 9am\" requests.\n"
        "## Session-only vs durable\n"
        "durable=true persists tasks to disk and survives restarts.\n"
        "Returns a job ID you can pass to CronDelete."
    )

    params = {
        "cron": ToolParam(
            type="string",
            description=(
                "Standard 5-field cron expression in local time: "
                "\"M H DoM Mon DoW\" (e.g. \"*/5 * * * *\" = every 5 minutes, "
                "\"30 14 28 2 *\" = Feb 28 at 2:30pm local once). "
                "Required for recurring=True. For recurring=False, use an ISO datetime string."
            ),
        ),
        "prompt": ToolParam(
            type="string",
            description="The prompt to enqueue at each fire time.",
        ),
        "recurring": ToolParam(
            type="boolean",
            description=(
                "true (default) = fire on every cron match until deleted. "
                "false = fire once at the next match, then auto-delete."
            ),
            required=False,
        ),
        "durable": ToolParam(
            type="boolean",
            description=(
                "true = persist to disk and survive restarts. "
                "false (default) = in-memory only, dies when session ends."
            ),
            required=False,
        ),
    }

    def __init__(self, cron_scheduler: CronScheduler):
        self._scheduler = cron_scheduler

    async def run(
        self,
        cron: str,
        prompt: str,
        recurring: bool = True,
        durable: bool = False,
    ) -> ToolResult:
        try:
            if not cron:
                return ToolResult.error("cron expression cannot be empty")

            # 校验 cron 表达式格式
            from ccserver.managers.cron.cron_parser import parse_cron_next_run
            from datetime import datetime, timezone
            try:
                parse_cron_next_run(cron, datetime.now(timezone.utc))
            except ValueError as e:
                return ToolResult.error(f"Invalid cron expression: {e}")

            mode = "recurring" if recurring else "once"

            task = self._scheduler.create(
                prompt=prompt,
                cron_expr=cron if recurring else None,
                run_at=None,
                jitter_max=0,
                durable=durable,
                mode=mode,
            )

            next_run_str = task.next_run_at.strftime("%Y-%m-%d %H:%M UTC")
            mode_str = "recurring" if recurring else "one-shot"
            durable_str = " (durable)" if durable else ""

            result = (
                f"Cron task created: [{task.task_id}] {mode_str}{durable_str}\n"
                f"Prompt: {prompt[:80]}{'...' if len(prompt) > 80 else ''}\n"
                f"Next run: {next_run_str}"
            )

            logger.info(
                "BTCronCreate | task_id={} mode={} cron={!r}",
                task.task_id, mode, cron,
            )
            return ToolResult.ok(result)

        except ValueError as e:
            return ToolResult.error(f"CronCreate failed: {e}")
        except Exception as e:
            logger.exception("BTCronCreate unexpected error | error={}", e)
            return ToolResult.error(f"Unexpected error: {e}")
