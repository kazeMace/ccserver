"""
http — 共享 httpx 异步客户端工厂。

集中各 adapter / factory 复用的 httpx.AsyncClient 配置（超时 / keepalive），
避免相同参数散落多处（原先 4-5 处重复）。改一处即全局生效（DRY）。

为何是这些参数：
  read/write/pool=600s —— 允许长耗时 LLM 调用（含 MCP 往返）。
  keepalive_expiry=5  —— 空闲连接最多保留 5 秒即丢弃，防止长操作后复用
                         已被服务端关闭的连接，避免 "incomplete chunked read"。
"""

from __future__ import annotations

import httpx


def make_async_http_client() -> httpx.AsyncClient:
    """
    构造统一配置的 httpx.AsyncClient（供 Anthropic / OpenAI SDK 作为 http_client）。

    Returns:
        httpx.AsyncClient：timeout=600s（connect=5s）、keepalive_expiry=5s。
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=600.0, write=600.0, pool=600.0),
        limits=httpx.Limits(keepalive_expiry=5),
    )
