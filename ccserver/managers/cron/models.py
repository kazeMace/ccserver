"""
managers/cron/models.py — ScheduledTask 数据模型。

统一定时任务模型，支持四种触发类型：
  - cron      : 日历级调度（每天/每周/每月），基于 5 字段 cron 表达式
  - interval  : 固定间隔（每 N 秒），支持秒级粒度
  - countdown : 倒计时（创建后 N 秒触发一次，自动删除）
  - once      : 固定时间点（明天 9 点），触发一次后自动删除

向后兼容：CronTask 是 ScheduledTask 的别名，旧代码无需修改。

状态机
────────────────────────────────────────────────────────────────────────────
scheduled ───到期触发──→ triggered ──检查生命周期──→ scheduled（循环）
                                              └───once/countdown──→ deleted
                                              └───超次数/超期─────→ expired
scheduled ───disable──→ paused（enabled=False，保留不触发）
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

from loguru import logger


# ─── 常量 ─────────────────────────────────────────────────────────────────────

TASK_PREFIX = "ct"  # 任务 ID 前缀（cron task 的 ct 前缀保持兼容）


# ─── ScheduledTask ────────────────────────────────────────────────────────────

@dataclass
class ScheduledTask:
    """
    统一定时任务数据模型，支持 cron / interval / countdown / once 四种触发类型。

    Attributes
    ──────────
    task_id : str
        唯一标识，格式为 "ct" + uuid 前 8 位（如 "ct3f2a1c0"）。

    prompt : str
        触发时注入 Agent inbox 的 prompt 文本。

    trigger_type : "cron" | "interval" | "countdown" | "once"
        触发类型，决定 next_run_at 的计算方式。

    cron_expr : str
        5 字段 cron 表达式（本地时间）。trigger_type=cron 时必填。
        格式："分 时 日 月 周"（分/时: 0-59/0-23, 日: 1-31, 月: 1-12, 周: 0-6）

    interval_seconds : int
        间隔秒数。trigger_type=interval 或 countdown 时使用。
        interval: 每次触发后重新计算 next_run_at = now + interval_seconds
        countdown: 创建时 next_run_at = created_at + interval_seconds，触发后删除

    run_at : datetime | None
        绝对触发时间（UTC）。trigger_type=once 时必填。

    enabled : bool
        是否启用。False 时任务保留但跳过触发（暂停状态）。

    max_triggers : int | None
        最大触发次数。None 表示无限次。达到上限后自动标记 expired 并删除。

    end_time : datetime | None
        截止时间（UTC）。None 表示永不过期。超过后自动标记 expired 并删除。

    next_run_at : datetime | None
        下次触发时间（UTC）。由调度器根据 trigger_type 自动计算。

    jitter_max : int
        最大随机延迟秒数。触发时随机延迟 [0, jitter_max] 秒，
        用于避免大量任务同时触发（防雷鸣效应）。默认 0 表示不启用。

    execution_policy : "fixed_rate" | "fixed_delay"
        interval 类型任务的执行策略，其他类型忽略此字段。
        - fixed_rate  : 按固定频率触发，不管上次执行是否完成。
                        next_run_at = 本次计划触发时间 + interval_seconds
                        适合心跳检测、状态轮询等要求时间对齐的场景。
        - fixed_delay : 上次触发后再等 interval_seconds 才触发下次。
                        next_run_at = 本次实际触发时间 + interval_seconds
                        适合任务执行时间不确定、不希望堆积的场景。

    durable : bool
        True 时任务写入磁盘，Session 重启后能恢复调度。

    status : str
        当前状态：scheduled（调度中）/ triggered（刚触发）/ deleted（已删除）
                  / expired（已过期，超次数或超期）/ paused（已暂停，enabled=False）。

    created_at : datetime
        UTC 时间戳，任务创建时间。

    last_triggered_at : datetime | None
        UTC 时间戳，最近一次触发时间。

    trigger_count : int
        累计触发次数（用于统计和生命周期检查）。

    meta : dict
        保留给用户或上层扩展使用的自定义字段（如 priority、source、channel 等）。

    jitter_seed : str
        用于 jitter 随机数生成的一致性 seed，保证相同 task_id 每次 jitter 结果一致。
    """

    # ── 基础字段 ──
    prompt: str
    trigger_type: Literal["cron", "interval", "countdown", "once"] = "interval"

    # ── 触发器配置 ──
    cron_expr: str = ""
    interval_seconds: int = 0
    run_at: Optional[datetime] = None

    # ── 生命周期控制 ──
    enabled: bool = True
    max_triggers: Optional[int] = None
    end_time: Optional[datetime] = None

    # ── 运行时状态 ──
    next_run_at: Optional[datetime] = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "scheduled"
    last_triggered_at: Optional[datetime] = None
    trigger_count: int = 0

    # ── 其他 ──
    jitter_max: int = 0
    # interval 类型执行策略：fixed_rate=按计划时间推进, fixed_delay=上次触发后延迟
    execution_policy: Literal["fixed_rate", "fixed_delay"] = "fixed_delay"
    durable: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    meta: dict = field(default_factory=dict)
    # 内部字段（不暴露给序列化）
    task_id: str = field(default="")
    jitter_seed: str = field(default="")

    def __post_init__(self):
        if not self.task_id:
            self.task_id = f"{TASK_PREFIX}{uuid.uuid4().hex[:8]}"
        if not self.jitter_seed:
            self.jitter_seed = self.task_id

    # ── 只读属性 ──

    @property
    def is_cron(self) -> bool:
        """是否 cron 类型任务。"""
        return self.trigger_type == "cron"

    @property
    def is_interval(self) -> bool:
        """是否 interval 类型任务。"""
        return self.trigger_type == "interval"

    @property
    def is_countdown(self) -> bool:
        """是否 countdown 类型任务。"""
        return self.trigger_type == "countdown"

    @property
    def is_once(self) -> bool:
        """是否 once 类型任务。"""
        return self.trigger_type == "once"

    @property
    def is_recurring(self) -> bool:
        """是否循环任务（cron 或 interval 会重复触发）。"""
        return self.trigger_type in ("cron", "interval")

    @property
    def is_done(self) -> bool:
        """是否已终结（deleted 或 expired）。"""
        return self.status in ("deleted", "expired")

    @property
    def is_paused(self) -> bool:
        """是否暂停（enabled=False 但未被删除）。"""
        return not self.enabled and self.status == "scheduled"

    # ── 生命周期检查 ──

    def check_lifecycle(self, now: Optional[datetime] = None) -> tuple[bool, str]:
        """
        检查任务生命周期状态。

        Args:
            now: 当前时间，默认使用 UTC now。

        Returns:
            (should_trigger, reason) 元组。
            should_trigger=True 表示可以正常触发；
            should_trigger=False 表示应跳过或删除，reason 说明原因。
        """
        if now is None:
            now = datetime.now(timezone.utc)

        if not self.enabled:
            return False, "paused"

        if self.end_time is not None and now >= self.end_time:
            self.status = "expired"
            return False, "end_time_reached"

        if self.max_triggers is not None and self.trigger_count >= self.max_triggers:
            self.status = "expired"
            return False, "max_triggers_reached"

        return True, "ok"

    # ── 状态变更 ──

    def mark_triggered(self, triggered_at: datetime) -> None:
        """
        标记任务已触发。

        更新 last_triggered_at、trigger_count。
        不修改 status（循环任务由调用方处理重排，一次性由调用方处理删除）。
        """
        assert self.status in ("scheduled", "triggered"), (
            f"ScheduledTask.mark_triggered: task {self.task_id} must be scheduled, "
            f"but is {self.status}"
        )
        self.last_triggered_at = triggered_at
        self.trigger_count += 1
        self.status = "triggered"
        logger.debug(
            "ScheduledTask triggered | task_id={} type={} count={}",
            self.task_id, self.trigger_type, self.trigger_count,
        )

    def mark_deleted(self) -> None:
        """标记任务已删除。幂等操作。"""
        if self.status == "deleted":
            logger.debug("ScheduledTask.delete skipped (already deleted) | task_id={}", self.task_id)
            return
        self.status = "deleted"
        logger.info("ScheduledTask deleted | task_id={}", self.task_id)

    def set_enabled(self, enabled: bool) -> None:
        """
        启用或禁用任务。

        Args:
            enabled: True 表示启用，False 表示暂停。
        """
        if self.enabled == enabled:
            return
        self.enabled = enabled
        action = "enabled" if enabled else "disabled"
        logger.info("ScheduledTask {} | task_id={}", action, self.task_id)

    # ── 序列化 ──

    def to_dict(self) -> dict:
        """
        序列化为字典，供 StorageAdapter 写入磁盘。

        不包含 in-memory 字段（如 jitter_seed）。
        """
        return {
            "task_id": self.task_id,
            "prompt": self.prompt,
            "trigger_type": self.trigger_type,
            "cron_expr": self.cron_expr,
            "interval_seconds": self.interval_seconds,
            "run_at": self.run_at.isoformat() if self.run_at else None,
            "enabled": self.enabled,
            "max_triggers": self.max_triggers,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "jitter_max": self.jitter_max,
            "execution_policy": self.execution_policy,
            "durable": self.durable,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_triggered_at": (
                self.last_triggered_at.isoformat() if self.last_triggered_at else None
            ),
            "trigger_count": self.trigger_count,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduledTask":
        """
        从字典反序列化，兼容旧版 CronTask 数据（无 trigger_type 等字段）。

        Args:
            data: 从存储加载的原始字典。

        Returns:
            ScheduledTask 实例。
        """
        # 兼容旧版 CronTask 数据
        trigger_type = data.get("trigger_type", "")
        if not trigger_type:
            # 旧数据：根据 mode 推断 trigger_type
            mode = data.get("mode", "recurring")
            cron_expr = data.get("cron_expr", "")
            if mode == "once":
                trigger_type = "once"
            elif cron_expr:
                trigger_type = "cron"
            else:
                trigger_type = "interval"

        # 兼容旧版 mode 字段
        if "mode" in data and trigger_type == "once":
            pass  # 旧版 once 就是 once

        def _parse_dt(key: str) -> Optional[datetime]:
            val = data.get(key)
            if val:
                return datetime.fromisoformat(val)
            return None

        return cls(
            task_id=data.get("task_id", ""),
            prompt=data.get("prompt", ""),
            trigger_type=trigger_type,  # type: ignore[arg-type]
            cron_expr=data.get("cron_expr", ""),
            interval_seconds=data.get("interval_seconds", 0),
            run_at=_parse_dt("run_at"),
            enabled=data.get("enabled", True),
            max_triggers=data.get("max_triggers"),
            end_time=_parse_dt("end_time"),
            next_run_at=_parse_dt("next_run_at") or datetime.now(timezone.utc),
            jitter_max=data.get("jitter_max", 0),
            execution_policy=data.get("execution_policy", "fixed_delay"),  # type: ignore[arg-type]
            durable=data.get("durable", False),
            status=data.get("status", "scheduled"),
            created_at=_parse_dt("created_at") or datetime.now(timezone.utc),
            last_triggered_at=_parse_dt("last_triggered_at"),
            trigger_count=data.get("trigger_count", 0),
            meta=data.get("meta", {}),
        )

    def __repr__(self) -> str:
        return (
            f"<ScheduledTask id={self.task_id} type={self.trigger_type} "
            f"next={self.next_run_at} enabled={self.enabled} "
            f"triggers={self.trigger_count}/{self.max_triggers or '∞'} "
            f"status={self.status}>"
        )


# ─── 向后兼容别名 ─────────────────────────────────────────────────────────────

CronTask = ScheduledTask
"""CronTask 是 ScheduledTask 的别名，保持向后兼容。"""


def generate_task_id() -> str:
    """
    生成唯一的任务 ID。

    Returns:
        形如 "ct3f2a1c0" 的任务 ID。
    """
    return f"{TASK_PREFIX}{uuid.uuid4().hex[:8]}"
