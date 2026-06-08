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
from dataclasses import dataclass
from typing import Literal
import calendar



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


# ─── 自然语言解析 ───────────────────────────────────────────────────────────────


@dataclass
class ScheduleSpec:
    """
    自然语言解析后的调度规范。

    Attributes:
        trigger_type: 触发类型（interval / countdown / once / cron）
        cron_expr:    cron 表达式，trigger_type=cron 时有效
        interval_seconds: 间隔/倒计时秒数，trigger_type=interval/countdown 时有效
        run_at:       绝对触发时间，trigger_type=once 时有效
        max_triggers: 最大触发次数（用户指定时）
        end_time:     截止时间（用户指定时）
    """
    trigger_type: Literal["interval", "countdown", "once", "cron"] = "interval"
    cron_expr: str = ""
    interval_seconds: int = 0
    run_at: datetime | None = None
    max_triggers: int | None = None
    end_time: datetime | None = None


def _parse_zh_number(text: str) -> int | None:
    """
    尝试解析中文数字或阿拉伯数字。

    Examples:
        "10" → 10
        "三十" → 30
        "五" → 5
    """
    text = text.strip()
    if text.isdigit():
        return int(text)

    # 中文数字映射
    zh_map = {
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
        "两": 2, "半": 30,  # 半小时=30分钟
    }

    # 简单中文数字处理（仅支持个位数和十位数）
    result = 0
    for ch in text:
        if ch in zh_map:
            v = zh_map[ch]
            if v == 10 and result > 0:
                result *= 10
            else:
                result += v
        elif ch not in ("个", "第", " "):
            return None

    return result if result > 0 else None


def _resolve_relative_time(
    num: int,
    unit: str,
    base: datetime,
) -> datetime:
    """
    根据数量和单位计算相对于 base 的未来时间。

    Args:
        num: 数量
        unit: 单位（秒/分/小时/天/周/月/年）
        base: 基准时间

    Returns:
        目标时间（UTC）
    """
    unit = unit.strip().lower()
    if unit in ("秒", "s", "sec", "secs", "second", "seconds"):
        return base + timedelta(seconds=num)
    if unit in ("分", "分钟", "m", "min", "mins", "minute", "minutes"):
        return base + timedelta(minutes=num)
    if unit in ("小时", "时", "h", "hr", "hrs", "hour", "hours"):
        return base + timedelta(hours=num)
    if unit in ("天", "日", "d", "day", "days"):
        return base + timedelta(days=num)
    if unit in ("周", "星期", "礼拜", "w", "week", "weeks"):
        return base + timedelta(weeks=num)
    if unit in ("月", "month", "months"):
        # 简单处理：每月按 30 天
        return base + timedelta(days=num * 30)
    if unit in ("年", "y", "year", "years"):
        return base + timedelta(days=num * 365)
    return base + timedelta(seconds=num)


def _resolve_absolute_time(text: str, base: datetime) -> datetime | None:
    """
    解析绝对时间点（如"明天早上9点"、"今天下午3点"）。

    注意：不处理"每天"/"每周"/"每月"等重复调度词（由调用方在调用前排除）。

    Args:
        text: 时间描述文本
        base: 基准时间

    Returns:
        解析成功返回 UTC datetime，失败返回 None。
    """
    import re
    text = text.strip().lower()

    # 排除重复调度词（否则"每天早上10点"会被误识别为 once）
    recurring_patterns = (
        "每天", "每周", "每月", "每年",
        "every day", "every week", "every month", "every year",
        "daily", "weekly", "monthly",
    )
    if any(p in text for p in recurring_patterns):
        return None

    result = base.replace(minute=0, second=0, microsecond=0)

    # 日期偏移
    day_offset = 0
    if "明天" in text or "明日" in text:
        day_offset = 1
    elif "后天" in text:
        day_offset = 2
    elif "今天" in text or "今日" in text:
        day_offset = 0
    elif "大后天" in text:
        day_offset = 3
    else:
        # 既没有明确日期词（如"明天"），也没有重复调度词（如"每天"），
        # 但有具体时间词（如"早上9点"）——此时应交给 cron 处理，不作为 once
        # 只有当包含"早上"/"下午"等时间段词（但没有具体小时数字）时，
        # 才能构成明确的 once
        has_time_indicator = any(k in text for k in ("早上", "中午", "下午", "晚上", "凌晨", "午夜", "上午", "下午", "傍晚"))
        has_hour_number = re.search(r'\d{1,2}\s*[:点]', text)
        if has_time_indicator and not has_hour_number:
            # 有时间段但无具体小时：可用
            pass
        elif has_hour_number:
            # 有具体小时但没有日期词（不是"明天"/"今天"/"后天"）：交给 cron
            return None

    result = result + timedelta(days=day_offset)

    # 时段词
    if "早上" in text or "早晨" in text or "上午" in text or "am" in text:
        result = result.replace(hour=9)
    elif "中午" in text or "午间" in text:
        result = result.replace(hour=12)
    elif "下午" in text or "傍晚" in text:
        result = result.replace(hour=15)
    elif "晚上" in text or "夜晚" in text or "pm" in text:
        result = result.replace(hour=20)
    elif "凌晨" in text:
        result = result.replace(hour=4)
    elif "午夜" in text or "半夜" in text or "零点" in text:
        result = result.replace(hour=0)

    # 提取具体小时数字
    import re
    # 匹配 "X点" 或 "X点Y分" 或 "X:Y"
    hour_match = re.search(r'(\d{1,2})\s*[:点]\s*(\d{1,2})?\s*(分)?', text)
    if hour_match:
        hour = int(hour_match.group(1))
        minute = int(hour_match.group(2)) if hour_match.group(2) else 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            result = result.replace(hour=hour, minute=minute)

    # 如果没有匹配到任何时间信息，返回 None
    if day_offset == 0 and not hour_match and not any(k in text for k in ("早上", "中午", "下午", "晚上", "凌晨", "午夜")):
        return None

    return result


def _resolve_duration(text: str, base: datetime) -> tuple[int | None, datetime | None]:
    """
    解析持续时间或截止条件（如"持续1小时"、"执行3次"）。

    Returns:
        (max_triggers, end_time) 元组，未解析到返回 (None, None)。
    """
    import re

    # 匹配 "持续N单位"
    duration_match = re.search(r'持续\s*(\d+)\s*(秒|分钟|分|小时|时|天|日|周|星期|月|年)', text)
    if duration_match:
        num = int(duration_match.group(1))
        unit = duration_match.group(2)
        end_time = _resolve_relative_time(num, unit, base)
        return None, end_time

    # 匹配 "执行N次" / "N次" / "触发N次"
    count_match = re.search(r'(?:执行|触发)?\s*(\d+)\s*次', text)
    if count_match:
        return int(count_match.group(1)), None

    # 匹配 "永久" / "一直" / "无限"
    if any(k in text for k in ("永久", "一直", "无限", "永远")):
        return None, None

    return None, None


def parse_natural_language_schedule(text: str, base: datetime | None = None) -> ScheduleSpec | None:
    """
    将自然语言描述解析为 ScheduleSpec。

    支持格式：
      - 每10秒/每10s/每十秒 → interval, interval_seconds=10
      - every 10 seconds → interval, interval_seconds=10
      - 每5分钟 → interval, interval_seconds=300
      - 30秒后/30s后 → countdown, interval_seconds=30
      - 5分钟后 → countdown, interval_seconds=300
      - 明天早上9点/明天9点 → once, run_at=明天9:00
      - 今天下午3点 → once, run_at=今天15:00
      - 每天早上10点 → cron, cron_expr="0 10 * * *"
      - 持续1小时 / 执行3次 → 附加 end_time / max_triggers（与主模式组合）

    Args:
        text: 用户自然语言输入。
        base: 基准时间，默认 UTC now。

    Returns:
        ScheduleSpec 或 None（无法解析时）。
    """
    import re

    if base is None:
        base = datetime.now(timezone.utc)

    original_text = text.strip()
    text_lower = original_text.lower()
    if not text_lower:
        return None

    # ── 0. 先提取全局修饰符（可出现在字符串任意位置） ──
    # "持续N单位" → end_time
    duration_match = re.search(r'持续\s*(\d+)\s*(秒|分钟|分|小时|时|天|日|周|月|年|个)', text_lower)
    modifier_end_time = None
    if duration_match:
        num = int(duration_match.group(1))
        unit = duration_match.group(2)
        modifier_end_time = _resolve_relative_time(num, unit, base)

    # "执行/触发/最多N次" → max_triggers
    count_match = re.search(r'(?:执行|触发|最多)?\s*(\d+)\s*次', text_lower)
    modifier_max_triggers = None
    if count_match:
        modifier_max_triggers = int(count_match.group(1))

    # ── 1. 解析 "每N单位"（interval，中文+英文） ──
    # 中文：每10秒  英文：every 10 seconds / every 10s
    interval_match = re.match(
        r'(?:每|every)\s*(\d+)\s*(秒|秒钟|秒s|s|分钟|分|m|小时|时|h|天|日|d|周|星期|w)',
        text_lower,
        re.IGNORECASE,
    )
    if interval_match:
        num = int(interval_match.group(1))
        unit = interval_match.group(2)
        seconds = _resolve_relative_time(num, unit, base) - base
        interval_seconds = int(seconds.total_seconds())
        return ScheduleSpec(
            trigger_type="interval",
            interval_seconds=interval_seconds,
            max_triggers=modifier_max_triggers,
            end_time=modifier_end_time,
        )

    # ── 2. 解析 "N单位后"（countdown） ──
    countdown_match = re.match(
        r'(\d+)\s*(秒|s|分钟|分|m|小时|时|h|天|日|d|周|星期|w)\s*后',
        text_lower,
        re.IGNORECASE,
    )
    if countdown_match:
        num = int(countdown_match.group(1))
        unit = countdown_match.group(2)
        seconds = _resolve_relative_time(num, unit, base) - base
        interval_seconds = int(seconds.total_seconds())
        return ScheduleSpec(
            trigger_type="countdown",
            interval_seconds=interval_seconds,
            max_triggers=modifier_max_triggers,
            end_time=modifier_end_time,
        )

    # ── 3. 解析 "X分钟后"（中文） ──
    zh_countdown_match = re.match(r'(\d+)\s*分钟?\s*后', text_lower)
    if zh_countdown_match:
        num = int(zh_countdown_match.group(1))
        interval_seconds = num * 60
        return ScheduleSpec(
            trigger_type="countdown",
            interval_seconds=interval_seconds,
            max_triggers=modifier_max_triggers,
            end_time=modifier_end_time,
        )

    # ── 4. 解析 "每天/每周/每月 X点"（cron，优先于 once） ──
    # 每天X点（如"每天早上10点"、"每天10点"、"每天10:30"）
    daily_match = re.search(
        r'每天\s*(?:(?:早上|上午|中午|下午|晚上|凌晨)?\s*)?(\d{1,2})\s*[:点]\s*(\d{1,2})?\s*(分)?',
        text_lower,
    )
    if daily_match:
        hour = int(daily_match.group(1))
        minute = int(daily_match.group(2)) if daily_match.group(2) else 0
        cron_expr = f"{minute} {hour} * * *"
        return ScheduleSpec(
            trigger_type="cron",
            cron_expr=cron_expr,
            max_triggers=modifier_max_triggers,
            end_time=modifier_end_time,
        )

    # ── 5. 解析 "明天/今天/后天 X点"（once，必须有明确日期词） ──
    # 注意：不要在有"每天/每周/每月"等重复词的情况下调用，
    # 否则"每天早上10点"会被错误识别为 once
    once_time = _resolve_absolute_time(original_text, base)
    if once_time is not None:
        return ScheduleSpec(
            trigger_type="once",
            run_at=once_time,
            max_triggers=modifier_max_triggers,
            end_time=modifier_end_time,
        )

    # ── 6. 兜底：纯数字秒数（如 "30" 或 "30s"） → 每 N 秒 interval ──
    simple_seconds = re.match(r'(\d+)\s*(秒|s)$', text_lower)
    if simple_seconds:
        interval_seconds = int(simple_seconds.group(1))
        return ScheduleSpec(
            trigger_type="interval",
            interval_seconds=interval_seconds,
            max_triggers=modifier_max_triggers,
            end_time=modifier_end_time,
        )

    # ── 7. 每周X / 每月X号（cron 模式）──
    # 每周X（如"每周一9点"）
    weekday_map = {
        "周一": 1, "星期一": 1,
        "周二": 2, "星期二": 2,
        "周三": 3, "星期三": 3,
        "周四": 4, "星期四": 4,
        "周五": 5, "星期五": 5,
        "周六": 6, "星期六": 6,
        "周日": 0, "星期日": 0, "周天": 0,
    }
    for zh_day, dow in weekday_map.items():
        if zh_day in text_lower:
            hour_match = re.search(r'(\d{1,2})\s*[:点]\s*(\d{1,2})?\s*(分)?', text_lower)
            hour = int(hour_match.group(1)) if hour_match else 9
            minute = int(hour_match.group(2)) if hour_match and hour_match.group(2) else 0
            cron_expr = f"{minute} {hour} * * {dow}"
            return ScheduleSpec(
                trigger_type="cron",
                cron_expr=cron_expr,
                max_triggers=modifier_max_triggers,
                end_time=modifier_end_time,
            )

    # 每月X号（如"每月1号9点"）
    monthly_match = re.search(r'每月\s*(\d{1,2})\s*号?\s*(\d{1,2})?\s*[:点]?\s*(\d{1,2})?\s*(分)?', text_lower)
    if monthly_match:
        dom = int(monthly_match.group(1))
        hour = int(monthly_match.group(2)) if monthly_match.group(2) else 9
        minute = int(monthly_match.group(3)) if monthly_match.group(3) else 0
        if 1 <= dom <= 31:
            cron_expr = f"{minute} {hour} {dom} * *"
            return ScheduleSpec(
                trigger_type="cron",
                cron_expr=cron_expr,
                max_triggers=modifier_max_triggers,
                end_time=modifier_end_time,
            )

    # 无法解析
    return None


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
