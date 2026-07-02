# ccserver/model_engine/errors.py
"""
model.errors — provider 无关的 LLM 错误类型与判定。

背景：
  各 provider（anthropic / openai）的 SDK 抛出各自的异常类型，
  L1 LLMCaller 若直接判断这些类型会被迫 import 全部 SDK（耦合）。

设计：
  每个 adapter 在 create()/stream() 中捕获自家 SDK 的"瞬态错误"（连接 / 超时 /
  429 限流 / 5xx），统一重抛为 TransientLLMError。L1 重试只认这一种类型。
  is_transient() 供 adapter 复用，集中"瞬态判定"规则，减少各 adapter 样板。
"""

from __future__ import annotations

import httpx


class TransientLLMError(Exception):
    """
    provider 无关的瞬态错误（连接 / 超时 / 限流 / 5xx），可重试。

    各 adapter 捕获自家 SDK 的瞬态异常后，包装为本类（保留原始异常为 __cause__）。
    """

    def __init__(self, message: str, cause: BaseException | None = None):
        super().__init__(message)
        # 显式保留原始异常，便于日志与排查
        if cause is not None:
            self.__cause__ = cause


# 判为瞬态的 httpx 异常类型（连接 / 超时 / 协议中断）
_TRANSIENT_HTTPX_TYPES = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)

# 判为瞬态的 HTTP 状态码（限流 / 服务端错误）
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}


def is_transient(exc: BaseException) -> bool:
    """
    判断一个异常是否属于"瞬态、可重试"。

    规则（provider 无关）：
      1. 已是 TransientLLMError → True
      2. httpx 连接/超时/协议类错误 → True
      3. 异常带 status_code 且在 {429,5xx} → True
      4. 其余 → False

    Args:
        exc: 任意异常实例。
    Returns:
        是否可重试。
    """
    if isinstance(exc, TransientLLMError):
        return True
    if isinstance(exc, _TRANSIENT_HTTPX_TYPES):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status in _TRANSIENT_STATUS:
        return True
    return False


def wrap_transient(exc: BaseException, context: str) -> None:
    """
    若异常是瞬态的，包装为 TransientLLMError 抛出（保留 __cause__）；否则原样抛出。

    供各 adapter 在 create()/stream() 的 except 块中复用，消除重复的判定+包装样板。

    Args:
        exc:     捕获到的原始异常。
        context: 简短上下文描述（如 "anthropic create"），拼进错误消息便于排查。
    Raises:
        TransientLLMError: exc 为瞬态错误时。
        原异常: 非瞬态时原样抛出。
    """
    assert isinstance(exc, BaseException), "wrap_transient: exc 必须是异常实例"
    if is_transient(exc):
        raise TransientLLMError(f"{context} transient: {exc}", exc) from exc
    raise exc
