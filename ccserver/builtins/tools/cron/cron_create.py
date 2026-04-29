"""
builtins/tools/cron_create.py — BTCronCreate 内置工具。

调度一个定时任务（支持 cron / interval / countdown / once 四种触发类型），
到期后将 prompt 注入 Agent inbox。

核心改进：
  - 支持自然语言解析（"每30秒"、"5分钟后"、"明天早上9点"）
  - 支持全部四种 trigger_type
  - 支持生命周期控制：max_triggers、end_time
  - 向后兼容：旧参数（cron, recurring, durable）仍可正常工作
"""

from datetime import datetime, timezone

from loguru import logger

from ..base import BuiltinTools, ToolParam, ToolResult
from ccserver.managers.cron import TaskScheduler, parse_natural_language_schedule, ScheduleSpec


class BTCronCreate(BuiltinTools):

    name = "CronCreate"
    risk = "medium"
    tags = ["cron", "scheduling"]

    description = (
        "Schedule a prompt to be enqueued at a future time. "
        "Supports 4 trigger types: cron, interval, countdown, once.\n"
        "## Natural language (recommended)\n"
        "Pass schedule as natural language in the 'schedule' parameter:\n"
        "  - 'every 30 seconds' / '每30秒' → interval\n"
        "  - '5 minutes later' / '5分钟后' → countdown\n"
        "  - 'tomorrow at 9am' / '明天早上9点' → once\n"
        "  - 'every day at 10am' / '每天早上10点' → cron\n"
        "## Manual mode\n"
        "Pass trigger_type + specific fields for precise control.\n"
        "## Lifecycle control\n"
        "  - max_triggers: auto-delete after N triggers (default: unlimited)\n"
        "  - end_time: auto-delete after this time (ISO datetime, default: never)\n"
        "Returns a job ID you can pass to CronDelete or CronUpdate."
    )

    params = {
        "schedule": ToolParam(
            type="string",
            description=(
                "Natural language schedule description. "
                "Examples: 'every 30 seconds', '5 minutes later', 'tomorrow at 9am', "
                "'每天早上10点', '每5分钟'. "
                "If this is provided, trigger_type and other schedule fields are ignored."
            ),
            required=False,
        ),
        "trigger_type": ToolParam(
            type="string",
            description=(
                "Trigger type: 'cron' | 'interval' | 'countdown' | 'once'. "
                "Required when schedule is not provided."
            ),
            required=False,
        ),
        "prompt": ToolParam(
            type="string",
            description="The prompt to enqueue at each fire time.",
        ),
        "cron": ToolParam(
            type="string",
            description=(
                "Standard 5-field cron expression in local time (for trigger_type=cron). "
                "Format: 'M H DoM Mon DoW'. Examples: '*/5 * * * *' = every 5 minutes, "
                "'0 9 * * 1-5' = 9am on weekdays."
            ),
            required=False,
        ),
        "interval_seconds": ToolParam(
            type="integer",
            description="Interval in seconds (for trigger_type=interval or countdown).",
            required=False,
        ),
        "run_at": ToolParam(
            type="string",
            description="ISO datetime string for one-shot trigger (for trigger_type=once).",
            required=False,
        ),
        "recurring": ToolParam(
            type="boolean",
            description="Backward compat: true=recurring, false=one-shot. Ignored when trigger_type is set.",
            required=False,
        ),
        "durable": ToolParam(
            type="boolean",
            description="Persist to disk and survive restarts. Default: false.",
            required=False,
        ),
        "max_triggers": ToolParam(
            type="integer",
            description="Max trigger count before auto-deletion. Default: unlimited.",
            required=False,
        ),
        "end_time": ToolParam(
            type="string",
            description="ISO datetime. Auto-delete after this time. Default: never.",
            required=False,
        ),
        "execution_policy": ToolParam(
            type="string",
            description=(
                "Execution policy for interval-type tasks (ignored for cron/once/countdown).\n"
                "  fixed_delay (default): wait N seconds AFTER the previous trigger before firing again. "
                "Use when tasks take variable time and you don't want overlapping runs.\n"
                "  fixed_rate  : fire every N seconds regardless of execution time. "
                "Use for polling, heartbeat, status checks."
            ),
            required=False,
        ),
    }

    def __init__(self, cron_scheduler: TaskScheduler):
        self._scheduler = cron_scheduler

    async def run(
        self,
        prompt: str,
        schedule: str = "",
        trigger_type: str = "",
        cron: str = "",
        interval_seconds: int = 0,
        run_at: str = "",
        recurring: bool = True,
        durable: bool = False,
        max_triggers: int = 0,
        end_time: str = "",
        execution_policy: str = "fixed_delay",
    ) -> ToolResult:
        try:
            if not prompt:
                return ToolResult.error("prompt cannot be empty")

            # ── 解析参数 ──
            spec: ScheduleSpec | None = None
            effective_trigger_type = trigger_type
            effective_cron = cron
            effective_interval = interval_seconds
            effective_run_at: datetime | None = None
            effective_max_triggers = max_triggers if max_triggers > 0 else None
            effective_end_time: datetime | None = None

            # 1. 自然语言优先
            if schedule:
                spec = parse_natural_language_schedule(schedule)
                if spec is None:
                    return ToolResult.error(
                        f"Could not parse schedule: {schedule!r}. "
                        "Try explicit trigger_type + cron/interval_seconds/run_at instead."
                    )
                effective_trigger_type = spec.trigger_type
                effective_cron = spec.cron_expr
                effective_interval = spec.interval_seconds
                effective_run_at = spec.run_at
                if spec.max_triggers is not None:
                    effective_max_triggers = spec.max_triggers
                if spec.end_time is not None:
                    effective_end_time = spec.end_time

            # 2. 如果没有 trigger_type，根据旧参数推断
            if not effective_trigger_type:
                if schedule or cron:
                    if not recurring:
                        effective_trigger_type = "once"
                    else:
                        effective_trigger_type = "cron"
                elif interval_seconds > 0:
                    effective_trigger_type = "interval"
                elif run_at:
                    effective_trigger_type = "once"
                else:
                    return ToolResult.error(
                        "Either 'schedule' (natural language) or 'trigger_type' + schedule fields must be provided."
                    )

            # 3. 解析 end_time 字符串
            if end_time:
                try:
                    effective_end_time = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                except ValueError as e:
                    return ToolResult.error(f"Invalid end_time format: {e}")

            # 4. 解析 run_at 字符串
            if run_at and not effective_run_at:
                try:
                    effective_run_at = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
                except ValueError as e:
                    return ToolResult.error(f"Invalid run_at format: {e}")

            # 5. 校验 cron 表达式
            if effective_trigger_type == "cron" and effective_cron:
                try:
                    from ccserver.managers.cron.cron_parser import parse_cron_next_run
                    parse_cron_next_run(effective_cron, datetime.now(timezone.utc))
                except ValueError as e:
                    return ToolResult.error(f"Invalid cron expression: {e}")

            # ── 校验 execution_policy ──
            valid_policies = ("fixed_rate", "fixed_delay")
            if execution_policy not in valid_policies:
                return ToolResult.error(
                    f"Invalid execution_policy: {execution_policy!r}. "
                    f"Must be one of: {', '.join(valid_policies)}"
                )

            # ── 创建任务 ──
            task = self._scheduler.create(
                prompt=prompt,
                trigger_type=effective_trigger_type,  # type: ignore[arg-type]
                cron_expr=effective_cron,
                interval_seconds=effective_interval,
                run_at=effective_run_at,
                jitter_max=0,
                durable=durable,
                enabled=True,
                max_triggers=effective_max_triggers,
                end_time=effective_end_time,
                execution_policy=execution_policy,  # type: ignore[arg-type]
            )

            # ── 构建返回信息 ──
            next_run_str = task.next_run_at.strftime("%Y-%m-%d %H:%M:%S UTC") if task.next_run_at else "N/A"
            type_label = task.trigger_type
            durable_str = " (durable)" if durable else ""
            policy_str = f" [{execution_policy}]" if task.trigger_type == "interval" else ""
            life_info = ""
            if effective_max_triggers:
                life_info += f" | max_triggers={effective_max_triggers}"
            if effective_end_time:
                life_info += f" | end_time={effective_end_time.strftime('%Y-%m-%d %H:%M UTC')}"

            result = (
                f"Scheduled task created: [{task.task_id}] {type_label}{policy_str}{durable_str}\n"
                f"Prompt: {prompt[:80]}{'...' if len(prompt) > 80 else ''}\n"
                f"Next run: {next_run_str}{life_info}"
            )

            logger.info(
                "BTCronCreate | task_id={} type={} next_run={}",
                task.task_id, task.trigger_type, next_run_str,
            )
            return ToolResult.ok(result)

        except ValueError as e:
            return ToolResult.error(f"CronCreate failed: {e}")
        except Exception as e:
            logger.exception("BTCronCreate unexpected error | error={}", e)
            return ToolResult.error(f"Unexpected error: {e}")
