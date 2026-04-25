"""
managers/cron/models.py — CronTask 数据模型。

描述单个定时任务的状态、字段定义、序列化/反序列化。

状态机
────────────────────────────────────────────────────────────────────────────
scheduled ───到期触发──→ triggered
    │                  ├── 一次性（mode=once）：立即删除
    │                  └── 循环（mode=recurring）：重新计算 next_run_at，回归 scheduled
    └── delete()──→ deleted
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

from loguru import logger


# ─── 常量 ─────────────────────────────────────────────────────────────────────

CRON_TASK_PREFIX = "ct"  # cron task ID 前缀，与 AgentTaskState 的 "a" 前缀区分


# ─── CronTask ────────────────────────────────────────────────────────────────

@dataclass
class CronTask:
    """
    单个定时任务的数据模型。

    Attributes
    ──────────
    task_id : str
        唯一标识，格式为 "ct" + uuid 前 8 位（如 "ct3f2a1c0"）。

    prompt : str
        触发时注入到 Agent inbox 的 prompt 文本。

    cron_expr : str
        5 字段 cron 表达式（本地时间）。仅循环任务有值；一次性任务为空。
        格式："分 时 日 月 周"（分/时: 0-59/0-23, 日: 1-31, 月: 1-12, 周: 0-6）

    mode : "once" | "recurring"
        任务类型。
        - once     : 一次性任务，到期触发一次后自动删除
        - recurring: 循环任务，每次触发后重新计算 next_run_at

    next_run_at : datetime
        UTC 时间，下次触发的绝对时间。

    jitter_max : int
        最大随机延迟秒数。触发时随机延迟 [0, jitter_max] 秒，
        用于避免大量客户端同时在整点触发（防雷鸣效应）。
        默认 0 表示不启用 jitter。

    durable : bool
        True 时任务写入磁盘，Session 重启后能恢复调度。

    status : str
        当前状态：pending（已创建未调度）/ scheduled（调度中）/ deleted（已删除）。

    created_at : datetime
        UTC 时间戳，任务创建时间。

    last_triggered_at : datetime | None
        UTC 时间戳，最近一次触发时间。

    trigger_count : int
        累计触发次数（用于统计和调试）。

    meta : dict
        保留给用户或上层扩展使用的自定义字段（如 priority、source 等）。

    jitter_seed : str
        用于 jitter 随机数生成的一致性 seed，
        保证相同 task_id 每次 jitter 结果一致。
    """

    prompt: str
    cron_expr: str = ""
    mode: Literal["once", "recurring"] = "recurring"
    next_run_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    jitter_max: int = 0
    durable: bool = False
    status: str = "scheduled"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_triggered_at: Optional[datetime] = None
    trigger_count: int = 0
    meta: dict = field(default_factory=dict)
    # 内部字段（不暴露给序列化）
    task_id: str = field(default="")
    jitter_seed: str = field(default="")

    def __post_init__(self):
        if not self.task_id:
            self.task_id = f"{CRON_TASK_PREFIX}{uuid.uuid4().hex[:8]}"
        if not self.jitter_seed:
            self.jitter_seed = self.task_id

    # ── 只读属性 ────────────────────────────────────────────────────────────

    @property
    def is_once(self) -> bool:
        """是否一次性任务。"""
        return self.mode == "once"

    @property
    def is_recurring(self) -> bool:
        """是否循环任务。"""
        return self.mode == "recurring"

    @property
    def is_done(self) -> bool:
        """是否已终结（deleted）。"""
        return self.status == "deleted"

    # ── 状态变更 ──────────────────────────────────────────────────────────

    def mark_triggered(self, triggered_at: datetime) -> None:
        """
        标记任务已触发。

        更新 last_triggered_at、trigger_count。
        不修改 status（一次性由调用方处理删除，循环由调用方处理重排）。
        """
        assert self.status == "scheduled", (
            f"CronTask.mark_triggered: task {self.task_id} must be scheduled, but is {self.status}"
        )
        self.last_triggered_at = triggered_at
        self.trigger_count += 1
        logger.debug(
            "CronTask triggered | task_id={} mode={} count={}",
            self.task_id, self.mode, self.trigger_count,
        )

    def mark_deleted(self) -> None:
        """标记任务已删除。幂等操作。"""
        if self.status == "deleted":
            logger.debug("CronTask.delete skipped (already deleted) | task_id={}", self.task_id)
            return
        self.status = "deleted"
        logger.info("CronTask deleted | task_id={}", self.task_id)

    # ── 序列化 ──────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """
        序列化为字典，供 StorageAdapter 写入磁盘。

        不包含 in-memory 字段（如 jitter_seed）。
        """
        return {
            "task_id": self.task_id,
            "prompt": self.prompt,
            "cron_expr": self.cron_expr,
            "mode": self.mode,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "jitter_max": self.jitter_max,
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
    def from_dict(cls, data: dict) -> "CronTask":
        """
        从字典反序列化。

        Args:
            data: 从存储加载的原始字典。

        Returns:
            CronTask 实例。
        """
        next_run_at = None
        if data.get("next_run_at"):
            next_run_at = datetime.fromisoformat(data["next_run_at"])
        created_at = None
        if data.get("created_at"):
            created_at = datetime.fromisoformat(data["created_at"])
        last_triggered_at = None
        if data.get("last_triggered_at"):
            last_triggered_at = datetime.fromisoformat(data["last_triggered_at"])

        return cls(
            task_id=data.get("task_id", ""),
            prompt=data.get("prompt", ""),
            cron_expr=data.get("cron_expr", ""),
            mode=data.get("mode", "recurring"),
            next_run_at=next_run_at or datetime.now(timezone.utc),
            jitter_max=data.get("jitter_max", 0),
            durable=data.get("durable", False),
            status=data.get("status", "scheduled"),
            created_at=created_at or datetime.now(timezone.utc),
            last_triggered_at=last_triggered_at,
            trigger_count=data.get("trigger_count", 0),
            meta=data.get("meta", {}),
        )

    def __repr__(self) -> str:
        return (
            f"<CronTask id={self.task_id} mode={self.mode} "
            f"cron={self.cron_expr!r} next={self.next_run_at} "
            f"durable={self.durable} status={self.status}>"
        )


# ─── 工具函数 ────────────────────────────────────────────────────────────────

def generate_task_id() -> str:
    """
    生成唯一的 Cron 任务 ID。

    Returns:
        形如 "ct3f2a1c0" 的任务 ID。
    """
    return f"{CRON_TASK_PREFIX}{uuid.uuid4().hex[:8]}"
