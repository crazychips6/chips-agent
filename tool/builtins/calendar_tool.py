"""calendar 工具 — 日期时间查询与计算

使用场景：
  - 查询当前日期、时间、时区
  - 获取某月的日历（含星期）
  - 计算两个日期之间的天数
  - 推算 N 天后的日期
  - 查询某日是星期几

零外部依赖（仅 Python stdlib）。
"""

from __future__ import annotations

import calendar as _calendar
import datetime
import json
import time

from tool.registry import registry


def _handle(args: dict) -> str:
    action = args.get("action", "now")

    if action == "now":
        return _do_now()
    if action == "month":
        return _do_month(args)
    if action == "diff":
        return _do_diff(args)
    if action == "weekday":
        return _do_weekday(args)
    if action == "add":
        return _do_add(args)

    return json.dumps({"error": f"未知操作: {action}（支持: now, month, diff, weekday, add）"})


# ── now ──


def _do_now() -> str:
    now = datetime.datetime.now()
    return json.dumps({
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "timezone": time.tzname[0] if time.daylight else time.tzname[0],
        "utc_offset": time.strftime("%z"),
        "weekday": now.strftime("%A"),
        "weekday_short": now.strftime("%a"),
        "iso_weekday": now.isoweekday(),
        "year": now.year,
        "month": now.month,
        "day": now.day,
        "hour": now.hour,
        "minute": now.minute,
        "second": now.second,
        "timestamp": now.timestamp(),
        "is_leap_year": now.year % 4 == 0 and (now.year % 100 != 0 or now.year % 400 == 0),
    }, ensure_ascii=False)


# ── month ──


def _do_month(args: dict) -> str:
    now = datetime.datetime.now()
    year = args.get("year", now.year)
    month = args.get("month", now.month)

    if not isinstance(year, int) or not isinstance(month, int):
        return json.dumps({"error": "year 和 month 必须为整数"})
    if month < 1 or month > 12:
        return json.dumps({"error": "month 必须在 1-12 之间"})

    cal = _calendar.TextCalendar()
    lines = cal.formatmonth(year, month).splitlines()

    _, days_in_month = _calendar.monthrange(year, month)
    first_weekday = datetime.date(year, month, 1).weekday()  # 0=Monday

    info = {
        "title": f"{year}年{month}月",
        "year": year,
        "month": month,
        "days_in_month": days_in_month,
        "first_weekday": first_weekday,
        "first_weekday_name": datetime.date(year, month, 1).strftime("%A"),
        "is_current_month": (
            year == now.year and month == now.month
        ),
        "calendar_text": "\n".join(lines),
    }
    return json.dumps(info, ensure_ascii=False)


# ── diff ──


def _do_diff(args: dict) -> str:
    from_str = args.get("from", "")
    to_str = args.get("to", "")

    if not from_str or not to_str:
        return json.dumps({"error": "需要 from 和 to 参数（YYYY-MM-DD）"})

    try:
        d1 = _parse_date(from_str)
        d2 = _parse_date(to_str)
    except ValueError as e:
        return json.dumps({"error": f"日期格式错误: {e}（请使用 YYYY-MM-DD）"})

    delta = d2 - d1
    return json.dumps({
        "from": from_str,
        "to": to_str,
        "days": delta.days,
        "direction": "future" if delta.days > 0 else "past" if delta.days < 0 else "same_day",
    }, ensure_ascii=False)


# ── weekday ──


def _do_weekday(args: dict) -> str:
    date_str = args.get("date", "")
    if not date_str:
        return json.dumps({"error": "需要 date 参数（YYYY-MM-DD）"})

    try:
        d = _parse_date(date_str)
    except ValueError as e:
        return json.dumps({"error": f"日期格式错误: {e}（请使用 YYYY-MM-DD）"})

    weekdays_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday_en = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    return json.dumps({
        "date": date_str,
        "weekday": weekday_en[d.weekday()],
        "weekday_short": d.strftime("%a"),
        "weekday_cn": weekdays_cn[d.weekday()],
        "iso_weekday": d.isoweekday(),
        "is_weekend": d.weekday() >= 5,
    }, ensure_ascii=False)


# ── add ──


def _do_add(args: dict) -> str:
    date_str = args.get("date", "")
    days = args.get("days", 0)

    if not date_str:
        return json.dumps({"error": "需要 date 参数（YYYY-MM-DD）"})
    if not isinstance(days, int):
        return json.dumps({"error": "days 必须为整数"})

    try:
        d = _parse_date(date_str)
    except ValueError as e:
        return json.dumps({"error": f"日期格式错误: {e}（请使用 YYYY-MM-DD）"})

    result = d + datetime.timedelta(days=days)
    return json.dumps({
        "original_date": date_str,
        "days_added": days,
        "result_date": result.strftime("%Y-%m-%d"),
        "result_weekday": result.strftime("%A"),
        "result_weekday_short": result.strftime("%a"),
    }, ensure_ascii=False)


# ── 辅助 ──


def _parse_date(s: str) -> datetime.date:
    """解析 YYYY-MM-DD 格式的日期字符串。"""
    parts = s.strip().split("-")
    if len(parts) != 3:
        raise ValueError(f"无法解析 '{s}'")
    return datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))


# ── Schema & 注册 ──


CALENDAR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "calendar",
        "description": (
            "日期时间查询与计算工具。支持五个操作：\n\n"
            "1. now — 获取当前日期、时间、时区、星期\n"
            "2. month — 获取指定月份的日历（year, month），含天数、首日星期\n"
            "3. diff — 计算两个日期之间的天数（from, to，格式 YYYY-MM-DD）\n"
            "4. weekday — 查询某日是星期几（date，格式 YYYY-MM-DD）\n"
            "5. add — 推算 N 天后的日期（date, days），days 可为负数\n\n"
            "当你需要可靠的日期计算时使用此工具，不要依赖自己的训练数据推算。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["now", "month", "diff", "weekday", "add"],
                    "description": "操作类型",
                },
                "year": {
                    "type": "integer",
                    "description": "年份（month 操作使用，默认当前年）",
                },
                "month": {
                    "type": "integer",
                    "description": "月份 1-12（month 操作使用，默认当前月）",
                },
                "from": {
                    "type": "string",
                    "description": "起始日期 YYYY-MM-DD（diff 操作使用）",
                },
                "to": {
                    "type": "string",
                    "description": "结束日期 YYYY-MM-DD（diff 操作使用）",
                },
                "date": {
                    "type": "string",
                    "description": "日期 YYYY-MM-DD（weekday/add 操作使用）",
                },
                "days": {
                    "type": "integer",
                    "description": "天数（add 操作使用，可为负数）",
                },
            },
            "required": ["action"],
        },
    },
}

registry.register(
    name="calendar",
    toolset="calendar",
    schema=CALENDAR_SCHEMA,
    handler=_handle,
)
