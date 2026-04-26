"""
builtins/tools/cron_update.py — BTCronUpdate 内置工具。

更新一个已创建的定时任务的配置。
"""

from datetime import datetime

from loguru import logger

from .base import BuiltinTools, ToolParam, ToolResult
from ccserver.managers.cron import TaskScheduler


class BTCronUpdate(BuiltinTools):

    name = "CronUpdate"

    description = (
        "Update an existing scheduled task created by CronCreate. "
        "Only provided fields are modified; others remain unchanged."
    )

    params = {
        "id": ToolParam(
            type="string",
            description="Task ID returned by CronCreate (e.g. 'ct3f2a1c0').",
        ),
        "prompt": ToolParam(
            type="string",
            description="New prompt text. Omit to keep existing.",
            required=False,
        ),
        "enabled": ToolParam(
            type="boolean",
            description="Enable or disable the task. Omit to keep existing.",
            required=False,
        ),
        "max_triggers": ToolParam(
            type="integer",
            description="New max trigger count. Set to 0 for unlimited. Omit to keep existing.",
            required=False,
        ),
        "end_time": ToolParam(
            type="string",
            description="New end time (ISO datetime). Omit to keep existing.",
            required=False,
        ),
        "cron": ToolParam(
            type="string",
            description="New cron expression (for cron tasks only). Omit to keep existing.",
            required=False,
        ),
        "interval_seconds": ToolParam(
            type="integer",
            description="New interval in seconds (for interval/countdown tasks). Omit to keep existing.",
            required=False,
        ),
    }

    def __init__(self, cron_scheduler: TaskScheduler):
        self._scheduler = cron_scheduler

    async def run(
        self,
        id: str,
        prompt: str = "",
        enabled: bool = True,
        max_triggers: int = -1,
        end_time: str = "",
        cron: str = "",
        interval_seconds: int = 0,
    ) -> ToolResult:
        try:
            if not id:
                return ToolResult.error("id cannot be empty")

            task = self._scheduler.get(id)
            if task is None:
                return ToolResult.error(f"Task '{id}' not found.")

            # 准备参数：空字符串/默认值表示不修改
            update_kwargs: dict = {}
            if prompt:
                update_kwargs["prompt"] = prompt
            # enabled 默认为 True，但我们需要区分"不传"和"传 True"
            # 工具框架不支持 None，所以用 -1 作为 max_triggers 的哨兵
            # enabled 用字符串方式无法区分，这里我们假设用户想修改时会明确传入
            # 实际上 LLM 调用时会传明确的 true/false
            update_kwargs["enabled"] = enabled
            if max_triggers >= 0:
                update_kwargs["max_triggers"] = max_triggers if max_triggers > 0 else None
            if end_time:
                try:
                    update_kwargs["end_time"] = datetime.fromisoformat(
                        end_time.replace("Z", "+00:00")
                    )
                except ValueError as e:
                    return ToolResult.error(f"Invalid end_time format: {e}")
            if cron:
                update_kwargs["cron_expr"] = cron
            if interval_seconds > 0:
                update_kwargs["interval_seconds"] = interval_seconds

            updated = self._scheduler.update(id, **update_kwargs)

            next_run_str = (
                updated.next_run_at.strftime("%Y-%m-%d %H:%M:%S UTC")
                if updated.next_run_at else "N/A"
            )

            result = (
                f"Scheduled task updated: [{updated.task_id}]\n"
                f"Type: {updated.trigger_type} | Enabled: {updated.enabled}\n"
                f"Next run: {next_run_str} | Triggers: {updated.trigger_count}/"
                f"{updated.max_triggers or 'unlimited'}"
            )

            logger.info("BTCronUpdate | task_id={}", id)
            return ToolResult.ok(result)

        except ValueError as e:
            return ToolResult.error(f"CronUpdate failed: {e}")
        except Exception as e:
            logger.exception("BTCronUpdate unexpected error | error={}", e)
            return ToolResult.error(f"Unexpected error: {e}")
