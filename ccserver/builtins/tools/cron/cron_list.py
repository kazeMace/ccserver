"""
builtins/tools/cron_list.py — BTCronList 内置工具。

列出当前所有定时任务（支持新 ScheduledTask 模型）。
"""

from loguru import logger

from ..base import BuiltinTools, ToolResult
from ccserver.managers.cron import TaskScheduler, cron_to_human


class BTCronList(BuiltinTools):

    name = "CronList"
    risk = "medium"
    tags = ["cron", "scheduling"]

    description = (
        "List all scheduled tasks in this session. "
        "Shows task ID, trigger type, schedule, next run time, trigger count, and lifecycle info."
    )

    params = {}

    def __init__(self, cron_scheduler: TaskScheduler):
        self._scheduler = cron_scheduler

    async def run(self) -> ToolResult:
        try:
            tasks = self._scheduler.list_all()

            if not tasks:
                return ToolResult.ok("No scheduled tasks.")

            lines = []
            for t in tasks:
                # 构建 schedule 描述
                if t.is_cron:
                    schedule_desc = cron_to_human(t.cron_expr) if t.cron_expr else "cron"
                elif t.is_interval:
                    schedule_desc = f"every {t.interval_seconds}s"
                elif t.is_countdown:
                    schedule_desc = f"after {t.interval_seconds}s"
                elif t.is_once:
                    schedule_desc = "one-shot"
                else:
                    schedule_desc = t.trigger_type

                # 下次运行时间
                next_str = (
                    t.next_run_at.strftime("%Y-%m-%d %H:%M:%S UTC")
                    if t.next_run_at else "N/A"
                )

                # 生命周期信息
                life_parts = []
                if not t.enabled:
                    life_parts.append("PAUSED")
                if t.max_triggers is not None:
                    life_parts.append(f"max={t.max_triggers}")
                if t.end_time is not None:
                    life_parts.append(f"until={t.end_time.strftime('%m-%d %H:%M')}")
                life_str = f" | {' | '.join(life_parts)}" if life_parts else ""

                # 状态
                status_str = t.status
                if t.is_done:
                    status_str = f"[{t.status}]"

                lines.append(
                    f"- [{t.task_id}] {t.trigger_type} | schedule={schedule_desc!r} | "
                    f"next={next_str} | triggers={t.trigger_count}{life_str} | {status_str}"
                    + (" | durable" if t.durable else "")
                )

            header = f"Scheduled tasks ({len(tasks)}):\n"
            return ToolResult.ok(header + "\n".join(lines))

        except Exception as e:
            logger.exception("BTCronList unexpected error | error={}", e)
            return ToolResult.error(f"Unexpected error: {e}")
