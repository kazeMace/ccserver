"""
managers/cron/cron_parser.py — 标准 5 字段 cron 表达式解析器。

支持字段（按 crontab 顺序）：
  分(0-59)  时(0-23)  日(1-31)  月(1-12)  周(0-6, 0=周日)

支持语法：
  *        — 任意值
  N        — 具体值（如 5）
  */N      — 每 N 个单位（如 */15 分 = 每 15 分钟）
  N-M      — 范围（如 9-17 = 9点到17点）
  N-M/S    — 步进范围（如 1-31/3 = 每月1,4,7...31日）
  N,M,O    — 列表（如 1,15 = 每月1号和15号）

用法：
  next_time = parse_cron_next_run("*/5 * * * *", datetime.now(timezone.utc))
  → 返回 base 时间之后最近一次匹配的分刻度时间
"""

from datetime import datetime, timezone, timedelta
import calendar
from typing import Optional

from loguru import logger


# ─── 字段范围定义 ─────────────────────────────────────────────────────────────

_FIELD_RANGES = [
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day of month
    (1, 12),   # month
    (0, 6),    # day of week (0=Sunday)
]

# 月份名称映射（支持 "JAN" ~ "DEC" 和 "jan" ~ "dec"）
_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
# 星期名称映射
_DOW_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3,
    "thu": 4, "fri": 5, "sat": 6,
    "0": 0, "1": 1, "2": 2, "3": 3,
    "4": 4, "5": 5, "6": 6, "7": 0,
}


def _expand_field(field: str, fmin: int, fmax: int) -> set[int]:
    """
    解析单个 cron 字段，返回所有匹配值的集合。

    Args:
        field:  字段字符串（如 "*", "*/5", "1-5", "1,3,5"）
        fmin:   字段最小值（含）
        fmax:   字段最大值（含）

    Returns:
        匹配值的有序集合。

    Raises:
        ValueError: 字段格式无效或值超出范围。
    """
    result: set[int] = set()
    parts = field.split(",")

    for part in parts:
        part = part.strip()

        # 通配符 *
        if part == "*":
            for v in range(fmin, fmax + 1):
                result.add(v)
            continue

        # */N 步进
        if part.startswith("*/"):
            step_str = part[2:]
            if not step_str.isdigit():
                raise ValueError(f"Invalid step value: {step_str!r}")
            step = int(step_str)
            if step <= 0:
                raise ValueError(f"Step must be positive: {step}")
            for v in range(fmin, fmax + 1, step):
                result.add(v)
            continue

        # N-M 或 N-M/S 范围
        if "-" in part:
            if "/" in part:
                range_part, step_str = part.split("/", 1)
                if not step_str.isdigit():
                    raise ValueError(f"Invalid step value: {step_str!r}")
                step = int(step_str)
                if step <= 0:
                    raise ValueError(f"Step must be positive: {step}")
            else:
                range_part = part
                step = 1

            range_part = range_part.strip()
            if "-" not in range_part:
                raise ValueError(f"Invalid range: {range_part!r}")
            lo_str, hi_str = range_part.split("-", 1)
            try:
                lo = int(lo_str.strip())
                hi = int(hi_str.strip())
            except ValueError:
                raise ValueError(f"Invalid range bounds: {range_part!r}")
            if lo < fmin or hi > fmax:
                raise ValueError(
                    f"Range {lo}-{hi} out of bounds ({fmin}-{fmax}) in {part!r}"
                )
            for v in range(lo, hi + 1, step):
                result.add(v)
            continue

        # 单个值（可能是名称）
        name_map = _MONTH_NAMES if fmin == 1 and fmax == 12 else _DOW_NAMES
        if part.upper() in name_map or part in name_map:
            v = name_map.get(part.upper(), name_map.get(part))
            if v is None:
                raise ValueError(f"Unknown name: {part!r}")
            if v < fmin or v > fmax:
                raise ValueError(f"Value {v} out of range ({fmin}-{fmax}) in {part!r}")
            result.add(v)
            continue

        # 纯数字
        if not part.isdigit():
            raise ValueError(f"Invalid field value: {part!r}")
        v = int(part)
        if v < fmin or v > fmax:
            raise ValueError(f"Value {v} out of range ({fmin}-{fmax}) in {field!r}")
        result.add(v)

    return result


def _days_in_month(year: int, month: int) -> int:
    """返回指定月份的天数。"""
    return calendar.monthrange(year, month)[1]


def _match_field(current: int, values: set[int], fmin: int, fmax: int) -> bool:
    """
    判断当前字段值是否在允许值集合中。

    如果 values == {fmin,...,fmax}（即全匹配），返回 True（等价于 *）。
    """
    if len(values) == fmax - fmin + 1:
        return True  # 全匹配，等价于 *
    return current in values


def parse_cron_next_run(cron_expr: str, base: datetime) -> datetime:
    """
    解析 5 字段 cron 表达式，计算 base 时间之后最近一次触发时间。

    Args:
        cron_expr: 标准 5 字段 cron 表达式（如 "*/5 * * * *"）
        base:      参考时间（UTC，建议用 aware datetime）

    Returns:
        base 之后最近一次匹配的 UTC datetime。

    Raises:
        ValueError: cron 表达式格式错误。

    注意：
        crontab 的 "周" 和 "日" 是 OR 关系：周日 OR 匹配日之一即可。
        crontab 的 "日" 和 "月" 是 AND 关系：必须同时满足。
    """
    cron_expr = cron_expr.strip()
    parts = cron_expr.split()
    if len(parts) != 5:
        raise ValueError(f"Cron expression must have exactly 5 fields, got: {cron_expr!r}")

    minute_vals = _expand_field(parts[0], 0, 59)
    hour_vals = _expand_field(parts[1], 0, 23)
    dom_vals = _expand_field(parts[2], 1, 31)   # day of month
    month_vals = _expand_field(parts[3], 1, 12)
    dow_vals = _expand_field(parts[4], 0, 6)     # day of week

    # 统一为 UTC aware datetime
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    else:
        base = base.astimezone(timezone.utc)

    # 从 base 开始，逐步推进时间，直到找到匹配
    current = base.replace(second=0, microsecond=0)

    # 最多扫描 2 年，防止死循环（极端情况下 366*24*60 次）
    max_iterations = 366 * 24 * 60
    for _ in range(max_iterations):
        month_match = _match_field(current.month, month_vals, 1, 12)
        if not month_match:
            # 跳到下月 1 日 00:00
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1, day=1, hour=0, minute=0)
            else:
                current = current.replace(month=current.month + 1, day=1, hour=0, minute=0)
            continue

        dow_match = _match_field(current.weekday(), dow_vals, 0, 6)
        dom_match = _match_field(current.day, dom_vals, 1, 31)

        # crontab 语义：周日 OR 匹配日（只要有一个条件满足即可）
        day_match = dow_match or dom_match
        if not day_match:
            # 跳到下一天 00:00
            current = current + timedelta(days=1)
            current = current.replace(hour=0, minute=0)
            continue

        hour_match = _match_field(current.hour, hour_vals, 0, 23)
        if not hour_match:
            # 找到下个小时中第一个匹配的；若已无更大小时则跳到次日
            candidates = [h for h in hour_vals if h > current.hour]
            if candidates:
                next_hour = min(candidates)
                current = current.replace(hour=next_hour, minute=min(minute_vals))
            else:
                # 当前 hour 已无可用更大值，跳到次日 00:00（重新匹配日期）
                current = current + timedelta(days=1)
                current = current.replace(hour=0, minute=min(minute_vals))
            continue

        minute_match = _match_field(current.minute, minute_vals, 0, 59)
        if not minute_match:
            candidates = [m for m in minute_vals if m > current.minute]
            if candidates:
                current = current.replace(minute=min(candidates))
            else:
                # 当前小时没有更晚的分钟，跳到下个小时
                current = current + timedelta(hours=1)
                current = current.replace(minute=min(minute_vals))
            continue

        # 分钟和小时都匹配了，检查日期条件
        if day_match:
            return current

        # 理论上到达此处 day_match 应为 True，若不是则跳到下一天
        current = current + timedelta(days=1)
        current = current.replace(hour=0, minute=0)

    raise ValueError(f"Cron expression {cron_expr!r} has no valid next run within 2 years from {base}")


def cron_to_human(cron_expr: str) -> str:
    """
    将 cron 表达式转换为人类可读的描述。

    Examples:
        "*/5 * * * *"  → "Every 5 minutes"
        "0 9 * * 1-5" → "At 9:00 AM on weekdays"
        "0 0 1 * *"    → "At midnight on the 1st of every month"
    """
    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return cron_expr

    minute, hour, dom, month, dow = parts

    # 每 N 分钟
    if minute.startswith("*/") and hour == "*" and dom == "*" and month == "*" and dow == "*":
        n = minute[2:]
        return f"Every {n} minute{'s' if n != '1' else ''}"

    # 每 N 小时
    if hour.startswith("*/") and minute == "0" and dom == "*" and month == "*" and dow == "*":
        n = hour[2:]
        return f"Every {n} hour{'s' if n != '1' else ''}"

    # 每小时整点（0 * * * *）
    if hour == "*" and minute == "0" and dom == "*" and month == "*" and dow == "*":
        return "Every hour"

    # 每天某时刻
    if dom == "*" and month == "*" and dow == "*":
        try:
            h = int(hour)
            m = int(minute)
            period = "AM" if h < 12 else "PM"
            h12 = h if h <= 12 else h - 12
            if h12 == 0:
                h12 = 12
            return f"At {h12}:{m:02d} {period}"
        except ValueError:
            pass

    # 工作日某时刻
    if dow in ("1-5", "1,2,3,4,5") and dom == "*" and month == "*":
        try:
            h = int(hour)
            m = int(minute)
            period = "AM" if h < 12 else "PM"
            h12 = h if h <= 12 else h - 12
            if h12 == 0:
                h12 = 12
            return f"At {h12}:{m:02d} {period} on weekdays"
        except ValueError:
            pass

    return cron_expr


# ─── Jitter ───────────────────────────────────────────────────────────────────

def compute_jitter_delay(jitter_max: int, seed: str) -> int:
    """
    基于 seed 的确定性 jitter 延迟。

    每次调用对相同 seed 返回相同结果，用于多个客户端共享同一 cron 表达式时
    的确定性错峰（避免每次生成不同的随机数）。

    Args:
        jitter_max: 最大延迟秒数，0 表示不启用
        seed:       任务 ID 等固定字符串

    Returns:
        [0, jitter_max] 之间的整数延迟秒数。
    """
    if jitter_max <= 0:
        return 0
    import hashlib
    # 将 seed 哈希为一个 0~jitter_max 的确定性整数
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    return h % (jitter_max + 1)
