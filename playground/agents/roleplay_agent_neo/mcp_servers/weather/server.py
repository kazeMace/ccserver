#!/usr/bin/env python3
"""
Weather MCP Server

从中国天气网获取实时天气，无需 API Key。
Tools: get_weather(city), get_weather_forecast(city, days)
"""

import sys
import re
import json
import urllib.parse
import urllib.request
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("weather")

_SCRIPT_DIR = Path(__file__).parent
_CITY_CODE_FILE = _SCRIPT_DIR / "weather_codes.txt"

# 天气描述 → emoji
_WEATHER_ICONS = {
    "晴": "☀️", "多云": "⛅", "阴": "☁️",
    "小雨": "🌦️", "中雨": "🌧️", "大雨": "🌧️", "暴雨": "⛈️",
    "雷阵雨": "⛈️", "阵雨": "🌦️",
    "小雪": "🌨️", "中雪": "❄️", "大雪": "❄️", "暴雪": "❄️",
    "雨夹雪": "🌨️", "冻雨": "🌨️",
    "沙尘暴": "🌪️", "浮尘": "🌫️", "扬沙": "🌫️",
    "雾": "🌫️", "霾": "🌫️",
}


def _weather_icon(desc: str) -> str:
    for key, icon in _WEATHER_ICONS.items():
        if key in desc:
            return icon
    return "🌤️"


def _load_city_codes() -> dict[str, str]:
    """加载城市代码映射表，返回 {城市名: 代码}"""
    codes: dict[str, str] = {}
    if not _CITY_CODE_FILE.exists():
        return codes
    for line in _CITY_CODE_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if len(parts) >= 2:
            codes[parts[0].strip()] = parts[1].strip()
    return codes


def _find_city_code(city: str) -> str | None:
    """精确匹配优先，再模糊匹配"""
    codes = _load_city_codes()
    if city in codes:
        return codes[city]
    # 模糊匹配
    for name, code in codes.items():
        if city in name or name in city:
            return code
    return None


def _fetch_html(city_code: str) -> str:
    url = f"https://www.weather.com.cn/weather/{city_code}.shtml"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    raw = urllib.request.urlopen(req, timeout=10).read()
    # 中国天气网使用 GBK 编码
    try:
        return raw.decode("gbk")
    except Exception:
        return raw.decode("utf-8", errors="replace")


def _parse_current(html: str, city: str) -> dict:
    """解析当前天气"""
    result = {
        "city": city,
        "weather": "未知",
        "temp": "未知",
        "temp_range": "未知",
        "cold_index": "—",
        "sport_index": "—",
        "dress_index": "—",
        "wash_index": "—",
        "uv_index": "—",
    }

    # 温度区间：如 "15/-3℃"
    m = re.search(r'(\d+)/(-?\d+)℃', html)
    if m:
        result["temp_range"] = f"{m.group(1)}/{m.group(2)}℃"
        result["temp"] = m.group(1) + "℃"

    # 天气描述（从 <p class="wea"> 或 title 提取）
    m = re.search(r'<p class="wea"[^>]*>([^<]+)</p>', html)
    if m:
        result["weather"] = m.group(1).strip()
    else:
        m = re.search(r'<title>([^<]*?)天气预报', html)
        if m:
            # title 格式: "XX天气预报..."，取前面的内容可能不含天气描述
            pass
        # 备用：匹配常见天气词
        m = re.search(
            r'(晴转多云|多云转晴|多云转阴|阴转多云|阴转小雨|小雨转中雨|'
            r'中雨转大雨|晴|多云|阴|小雨|中雨|大雨|暴雨|雷阵雨|阵雨|'
            r'小雪|中雪|大雪|暴雪|雨夹雪|冻雨|雾|霾|沙尘暴)',
            html
        )
        if m:
            result["weather"] = m.group(1)

    # 生活指数
    _index_patterns = [
        ("cold_index",  r"感冒[^：]*[：:]\s*([^<\n]{2,8})"),
        ("sport_index", r"运动[^：]*[：:]\s*([^<\n]{2,8})"),
        ("dress_index", r"穿衣[^：]*[：:]\s*([^<\n]{2,8})"),
        ("wash_index",  r"洗车[^：]*[：:]\s*([^<\n]{2,8})"),
        ("uv_index",    r"紫外线[^：]*[：:]\s*([^<\n]{2,8})"),
    ]
    for key, pattern in _index_patterns:
        m = re.search(pattern, html)
        if m:
            result[key] = m.group(1).strip()

    # 关键词匹配兜底（仿照 sh 脚本逻辑）
    if result["cold_index"] == "—":
        for kw, val in [("极易发感冒", "极易发"), ("易发感冒", "易发"),
                        ("较易发感冒", "较易发"), ("少发感冒", "少发")]:
            if kw in html:
                result["cold_index"] = val
                break

    if result["sport_index"] == "—":
        for kw, val in [("不宜运动", "不宜"), ("较不宜运动", "较不宜"),
                        ("较适宜运动", "较适宜"), ("适宜运动", "适宜")]:
            if kw in html:
                result["sport_index"] = val
                break

    if result["uv_index"] == "—":
        for kw, val in [("强紫外线", "强"), ("中等紫外线", "中等"), ("弱紫外线", "弱")]:
            if kw in html:
                result["uv_index"] = val
                break

    if result["wash_index"] == "—":
        for kw, val in [("不宜洗车", "不宜"), ("较适宜洗车", "较适宜"), ("适宜洗车", "适宜")]:
            if kw in html:
                result["wash_index"] = val
                break

    return result


def _parse_forecast(html: str, days: int) -> list[dict]:
    """解析未来多天预报，从 <ul class="t clearfix"> 提取"""
    forecasts = []

    # 匹配每天预报块：日期、天气、温度
    # 中国天气网7天预报在 <ul class="t clearfix"> 内，每个 <li> 为一天
    li_blocks = re.findall(r'<li[^>]*>(.*?)</li>', html, re.DOTALL)

    for block in li_blocks:
        if "℃" not in block:
            continue

        day: dict = {}

        # 日期
        m = re.search(r'(\d{1,2}日)', block)
        if m:
            day["date"] = m.group(1)
        else:
            # 今天/明天
            m = re.search(r'(今天|明天|后天)', block)
            day["date"] = m.group(1) if m else "—"

        # 天气描述
        m = re.search(r'<p[^>]*title="([^"]+)"', block)
        if m:
            day["weather"] = m.group(1)
        else:
            m = re.search(r'<p[^>]*>([晴多阴雨雪雾霾][^<]{0,8})</p>', block)
            day["weather"] = m.group(1) if m else "—"

        # 温度
        m = re.search(r'(\d+)℃.*?(-?\d+)℃', block, re.DOTALL)
        if m:
            day["high"] = m.group(1) + "℃"
            day["low"] = m.group(2) + "℃"
        else:
            m = re.search(r'(-?\d+)/(-?\d+)℃', block)
            if m:
                day["high"] = m.group(1) + "℃"
                day["low"] = m.group(2) + "℃"

        if "date" in day and "weather" in day:
            forecasts.append(day)

        if len(forecasts) >= days:
            break

    return forecasts


@mcp.tool()
def get_weather(city: str) -> str:
    """
    获取指定城市的当前天气。

    参数：
        city: 城市名称，支持中英文（如 "北京"、"上海"、"Tokyo"）

    返回：
        当前温度、天气状况、生活指数等信息。
    """
    code = _find_city_code(city)
    if not code:
        return f"未找到城市 '{city}'，请确认城市名称（如：北京、上海、广州）"

    try:
        html = _fetch_html(code)
    except Exception as e:
        print(f"[weather] fetch failed: {e}", file=sys.stderr)
        return f"获取天气失败: {e}"

    d = _parse_current(html, city)
    icon = _weather_icon(d["weather"])

    lines = [
        f"📍 {city} 今日天气",
        f"{icon} {d['weather']}  |  🌡️ 温度：{d['temp_range']}",
        "",
        "📊 生活指数",
        f"  🤧 感冒：{d['cold_index']}",
        f"  🏃 运动：{d['sport_index']}",
        f"  👔 穿衣：{d['dress_index']}",
        f"  🚗 洗车：{d['wash_index']}",
        f"  ☀️ 紫外线：{d['uv_index']}",
    ]
    return "\n".join(lines)


@mcp.tool()
def get_weather_forecast(city: str, days: int = 3) -> str:
    """
    获取指定城市未来几天的天气预报。

    参数：
        city: 城市名称，支持中英文（如 "北京"、"Paris"、"New York"）
        days: 预报天数，1-7 天（默认 3）

    返回：
        每天的最高/最低温度、天气状况等预报信息。
    """
    days = max(1, min(days, 7))
    code = _find_city_code(city)
    if not code:
        return f"未找到城市 '{city}'，请确认城市名称（如：北京、上海、广州）"

    try:
        html = _fetch_html(code)
    except Exception as e:
        print(f"[weather] fetch failed: {e}", file=sys.stderr)
        return f"获取天气预报失败: {e}"

    forecasts = _parse_forecast(html, days)
    if not forecasts:
        return f"解析 {city} 天气预报失败，请稍后重试"

    lines = [f"📍 {city} 天气预报（未来 {days} 天）", ""]
    for f in forecasts:
        icon = _weather_icon(f.get("weather", ""))
        high = f.get("high", "—")
        low = f.get("low", "—")
        lines.append(
            f"📅 {f['date']}  {icon} {f.get('weather', '—')}  "
            f"🌡️ {low} ~ {high}"
        )

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
