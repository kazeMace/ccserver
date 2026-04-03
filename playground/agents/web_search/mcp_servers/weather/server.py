#!/usr/bin/env python3
"""
Weather MCP Server

Provides weather information via open-meteo.com (free, no API key required).
Tools: get_weather(city), get_weather_forecast(city, days)
"""

import sys
import json
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("weather")

# WMO Weather Code → 中文描述
WMO_CODES = {
    0: "晴天", 1: "基本晴天", 2: "部分多云", 3: "阴天",
    45: "雾", 48: "冻雾",
    51: "小毛毛雨", 53: "中毛毛雨", 55: "大毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "冰粒",
    80: "小阵雨", 81: "中阵雨", 82: "强阵雨",
    85: "小阵雪", 86: "强阵雪",
    95: "雷暴", 96: "雷暴伴冰雹", 99: "强雷暴伴冰雹",
}

WIND_DIRS = ["北", "东北", "东北", "东北", "东", "东南", "东南", "东南",
             "南", "西南", "西南", "西南", "西", "西北", "西北", "西北"]


def _wind_direction(degrees: float) -> str:
    idx = round(degrees / 22.5) % 16
    return WIND_DIRS[idx]


def _geocode(city: str) -> tuple[float, float, str, str]:
    """Return (lat, lon, display_name, timezone) for a city name."""
    params = urllib.parse.urlencode({"name": city, "count": 1, "language": "zh", "format": "json"})
    url = f"https://geocoding-api.open-meteo.com/v1/search?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "curl/7.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=10).read())
    results = data.get("results")
    if not results:
        raise ValueError(f"找不到城市: {city}")
    r = results[0]
    name = r.get("name", city)
    country = r.get("country", "")
    display = f"{name}, {country}" if country else name
    return r["latitude"], r["longitude"], display, r.get("timezone", "auto")


def _fetch_weather(lat: float, lon: float, timezone: str, forecast_days: int = 1) -> dict:
    params = urllib.parse.urlencode({
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                   "wind_speed_10m,wind_direction_10m,weather_code,visibility",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "precipitation_sum,precipitation_probability_max",
        "timezone": timezone,
        "forecast_days": forecast_days,
    })
    url = f"https://api.open-meteo.com/v1/forecast?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "curl/7.0"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


@mcp.tool()
def get_weather(city: str) -> str:
    """
    获取指定城市的当前天气。

    参数：
        city: 城市名称，支持中英文（如 "北京"、"Shanghai"、"Tokyo"）

    返回：
        当前温度、体感温度、天气状况、湿度、风速、能见度等信息。
    """
    try:
        lat, lon, display, tz = _geocode(city)
        data = _fetch_weather(lat, lon, tz, forecast_days=1)
    except Exception as e:
        print(f"[weather] get_weather failed: {e}", file=sys.stderr)
        return f"获取天气失败: {e}"

    c = data["current"]
    temp = c["temperature_2m"]
    feels = c["apparent_temperature"]
    humidity = c["relative_humidity_2m"]
    wind_spd = c["wind_speed_10m"]
    wind_dir = _wind_direction(c["wind_direction_10m"])
    desc = WMO_CODES.get(c["weather_code"], f"天气代码 {c['weather_code']}")
    vis_km = round(c["visibility"] / 1000, 1)

    return (
        f"📍 {display} 当前天气\n"
        f"🌡️ 温度: {temp}°C（体感 {feels}°C）\n"
        f"🌤️ 天气: {desc}\n"
        f"💧 湿度: {humidity}%\n"
        f"💨 风速: {wind_spd} km/h，风向: {wind_dir}\n"
        f"👁️ 能见度: {vis_km} km"
    )


@mcp.tool()
def get_weather_forecast(city: str, days: int = 3) -> str:
    """
    获取指定城市未来几天的天气预报。

    参数：
        city: 城市名称，支持中英文（如 "北京"、"Paris"、"New York"）
        days: 预报天数，1-7 天（默认 3）

    返回：
        每天的最高/最低温度、天气状况、降水量、降水概率等预报信息。
    """
    days = max(1, min(days, 7))
    try:
        lat, lon, display, tz = _geocode(city)
        data = _fetch_weather(lat, lon, tz, forecast_days=days)
    except Exception as e:
        print(f"[weather] get_weather_forecast failed: {e}", file=sys.stderr)
        return f"获取天气预报失败: {e}"

    daily = data["daily"]
    lines = [f"📍 {display} 天气预报（未来 {days} 天）\n"]

    for i in range(days):
        date = daily["time"][i]
        max_t = daily["temperature_2m_max"][i]
        min_t = daily["temperature_2m_min"][i]
        code = daily["weather_code"][i]
        desc = WMO_CODES.get(code, f"天气代码 {code}")
        precip = daily["precipitation_sum"][i]
        precip_prob = daily["precipitation_probability_max"][i]

        lines.append(
            f"📅 {date}\n"
            f"   🌡️ {min_t}°C ~ {max_t}°C\n"
            f"   🌤️ {desc}\n"
            f"   🌧️ 降水: {precip} mm  概率: {precip_prob}%"
        )

    return "\n\n".join(lines)


if __name__ == "__main__":
    mcp.run()
