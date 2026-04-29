"""
builtins/tools/cron_delete.py — BTCronDelete 内置工具。

取消一个已创建的定时任务。
"""

from loguru import logger

from ..base import BuiltinTools, ToolParam, ToolResult
from ccserver.managers.cron import CronScheduler


class BTCronDelete(BuiltinTools):

    name = "CronDelete"
    risk = "medium"
    tags = ["cron", "scheduling"]

    description = (
        "Cancel a cron job previously scheduled with CronCreate. "
        "Removes it from the scheduler immediately."
    )

    params = {
        "id": ToolParam(
            type="string",
            description="Job ID returned by CronCreate (e.g. 'ct3f2a1c0').",
        ),
    }

    def __init__(self, cron_scheduler: CronScheduler):
        self._scheduler = cron_scheduler

    async def run(self, id: str) -> ToolResult:
        try:
            if not id:
                return ToolResult.error("id cannot be empty")

            deleted = self._scheduler.delete(id)
            if deleted:
                logger.info("BTCronDelete | task_id={}", id)
                return ToolResult.ok(f"Cron task [{id}] deleted.")
            else:
                return ToolResult.ok(f"Cron task [{id}] not found.")

        except Exception as e:
            logger.exception("BTCronDelete unexpected error | error={}", e)
            return ToolResult.error(f"Unexpected error: {e}")
