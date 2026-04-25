"""
builtins/tools/cron_list.py — BTCronList 内置工具。

列出当前所有定时任务。
"""

from loguru import logger

from .base import BuiltinTools, ToolParam, ToolResult
from ccserver.managers.cron import CronScheduler, cron_to_human


class BTCronList(BuiltinTools):

    name = "CronList"

    description = (
        "List all cron jobs scheduled via CronCreate in this session. "
        "Shows task ID, mode, schedule, next run time, and trigger count."
    )

    params = {}

    def __init__(self, cron_scheduler: CronScheduler):
        self._scheduler = cron_scheduler

    async def run(self) -> ToolResult:
        try:
            tasks = self._scheduler.list_all()

            if not tasks:
                return ToolResult.ok("No cron tasks scheduled.")

            lines = []
            for t in tasks:
                mode_label = "recurring" if t.is_recurring else "one-shot"
                next_str = t.next_run_at.strftime("%Y-%m-%d %H:%M UTC")
                human = cron_to_human(t.cron_expr) if t.cron_expr else t.mode
                lines.append(
                    f"- [{t.task_id}] {mode_label} | schedule={human!r} | "
                    f"next={next_str} | triggers={t.trigger_count}"
                    + (" | durable" if t.durable else "")
                )

            header = f"Cron tasks ({len(tasks)}):\n"
            return ToolResult.ok(header + "\n".join(lines))

        except Exception as e:
            logger.exception("BTCronList unexpected error | error={}", e)
            return ToolResult.error(f"Unexpected error: {e}")
