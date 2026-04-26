"""
managers/cron — 定时任务调度模块。

入口类：
    TaskScheduler — Session 级定时任务调度引擎（支持 cron/interval/countdown/once）
    ScheduledTask — 统一定时任务数据模型

子模块：
    models       — ScheduledTask / CronTask 数据模型
    cron_parser  — 5 字段 cron 表达式解析器 + 自然语言解析
    scheduler    — 调度引擎实现

向后兼容：
    CronScheduler = TaskScheduler（旧代码可直接使用）
    CronTask = ScheduledTask（旧代码可直接使用）
"""

from .scheduler import TaskScheduler, CronScheduler
from .models import ScheduledTask, CronTask
from .cron_parser import (
    parse_cron_next_run,
    cron_to_human,
    compute_jitter_delay,
    parse_natural_language_schedule,
    ScheduleSpec,
)

__all__ = [
    "TaskScheduler",
    "CronScheduler",
    "ScheduledTask",
    "CronTask",
    "parse_cron_next_run",
    "cron_to_human",
    "compute_jitter_delay",
    "parse_natural_language_schedule",
    "ScheduleSpec",
]
