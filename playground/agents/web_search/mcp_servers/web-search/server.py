#!/usr/bin/env python3
"""
Web Search MCP Server

Provides real-time web search capability via DuckDuckGo.
Tool: search_web(query, max_results)
"""

import sys

from mcp.server.fastmcp import FastMCP
from ddgs import DDGS

mcp = FastMCP("web-search")


@mcp.tool()
def search_web(query: str, max_results: int = 5) -> str:
    """
    通过 DuckDuckGo 搜索网页，获取实时信息。
    适用于：生词/新概念解释、娱乐内容（电影/音乐/游戏）、股价、天气等非新闻类查询。

    参数：
        query:       搜索关键词，建议经过改写以包含时间限定词。
        max_results: 最多返回结果数量（默认 5，最大 10）。

    返回：
        包含标题、URL、摘要的格式化搜索结果。
    """
    max_results = min(max_results, 10)
    try:
        raw = DDGS(timeout=15).text(query, max_results=max_results)
        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            }
            for r in raw
        ]
    except Exception as e:
        print(f"[web-search] search_web failed: {e}", file=sys.stderr)
        return f"Search failed: {e}"

    if not results:
        return "No results found."

    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r['title']}**")
        lines.append(f"   {r['url']}")
        lines.append(f"   {r['snippet']}\n")

    return "\n".join(lines)


@mcp.tool()
def search_news(query: str, max_results: int = 5) -> str:
    """
    通过 DuckDuckGo 搜索新闻，获取最新时事资讯。
    适用于：新闻、时事、赛事结果、娱乐动态等时效性强的查询。

    参数：
        query:       新闻搜索关键词，建议包含时间限定词以提高精度。
        max_results: 最多返回结果数量（默认 5，最大 10）。

    返回：
        包含标题、URL、发布日期、摘要的格式化新闻结果。
    """
    max_results = min(max_results, 10)
    try:
        raw = DDGS().news(query, max_results=max_results)
        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "date": r.get("date", ""),
                "snippet": r.get("body", ""),
            }
            for r in raw
        ]
    except Exception as e:
        print(f"[web-search] search_news failed: {e}", file=sys.stderr)
        return f"News search failed: {e}"

    if not results:
        return "No news found."

    lines = [f"News results for: {query}\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r['title']}** [{r['date']}]")
        lines.append(f"   {r['url']}")
        lines.append(f"   {r['snippet']}\n")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
