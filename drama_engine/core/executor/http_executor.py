"""HTTP 执行器 — 调用外部 HTTP 服务。

职责：构造 HTTP 请求 → 发送 → 解析 JSON 响应 → 返回。

DSL 可配参数:
    executor: http
    url: "https://..."          # 必填
    method: POST                # 可选，默认 POST
    headers: {...}              # 可选
    timeout_ms: 10000           # 可选，默认 10000
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import Any

from drama_engine.core.executor.base import BaseExecutor, ExecutorRequest, ExecutorResponse

logger = logging.getLogger(__name__)


class HttpExecutor(BaseExecutor):
    """HTTP 执行器。

    通过标准库 urllib 发送 HTTP 请求。
    使用 asyncio.to_thread 避免阻塞事件循环。
    """

    async def execute(self, request: ExecutorRequest) -> ExecutorResponse:
        """执行 HTTP 请求。

        request.config 必须包含 "url" 字段。
        request.payload 作为 JSON body 发送。
        """
        url = request.config.get("url")
        assert url, "HttpExecutor 要求 config 中包含 url 字段"

        method = str(request.config.get("method") or "POST").upper()
        headers = dict(request.config.get("headers") or {})
        headers.setdefault("Content-Type", "application/json")
        timeout_ms = int(request.config.get("timeout_ms") or 10000)
        timeout_s = timeout_ms / 1000

        # 在线程中执行同步 HTTP 请求，不阻塞事件循环
        try:
            result = await asyncio.to_thread(
                self._do_request, url, method, headers, request.payload, timeout_s
            )
        except Exception as exc:
            logger.error("[HttpExecutor] 请求失败 url=%s: %s", url, exc)
            return ExecutorResponse(success=False, error=str(exc), raw=exc)

        logger.debug("[HttpExecutor] 请求成功 url=%s", url)
        return ExecutorResponse(success=True, data=result, raw=result)

    def _do_request(
        self,
        url: str,
        method: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        """同步执行 HTTP 请求。"""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw_data = response.read().decode("utf-8")
            parsed = json.loads(raw_data)
            if isinstance(parsed, dict):
                return parsed
            return {"value": parsed}


__all__ = ["HttpExecutor"]
